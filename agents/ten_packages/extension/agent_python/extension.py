#
#
# Agora Real Time Engagement
# Created by Wei Hu in 2024-08.
# Copyright (c) 2024 Agora IO. All rights reserved.
#
#
from ten import (
    Extension,
    TenEnv,
    Cmd,
    Data,
    StatusCode,
    StatusCode,
    CmdResult,
)

import asyncio
import json
import time
import traceback
from typing import Iterable
import uuid

from .openai import OpenAIChatGPTConfig, OpenAIChatGPT
from .agent import Agent

CMD_IN_FLUSH = "flush"
CMD_IN_ON_USER_JOINED = "on_user_joined"
CMD_IN_ON_USER_LEFT = "on_user_left"
CMD_OUT_FLUSH = "flush"

DATA_OUT_NAME = "text_data"
CONTENT_DATA_OUT_NAME = "content_data"
DATA_OUT_PROPERTY_TEXT = "text"
DATA_OUT_PROPERTY_END_OF_SEGMENT = "end_of_segment"


class AgentExtension(Extension):
    def __init__(self, name: str):
        super().__init__(name)
        self.config1 = None
        self.agent = None
        self.ten_env = None
        self.users_count = 0
        self.last_reasoning_ts = 0
        self.greetings = ""

    def on_init(self, ten_env: TenEnv) -> None:
        ten_env.log_info("on_init")
        self.agent = Agent(self)
        self.ten_env = ten_env
        super().on_init(ten_env)

    def on_start(self, ten_env: TenEnv) -> None:
        ten_env.log_info("on_start")

        self.greetings = ten_env.get_property_string("greeting")
        self.config1 = OpenAIChatGPTConfig()
        self.config1.api_key = ten_env.get_property_string("llm1_key")
        self.config1.base_url = ten_env.get_property_string("llm1_url")
        self.config1.model = ten_env.get_property_string("llm1_model")
        self.config1.prompt = ten_env.get_property_string("llm1_prompt")

        # Create agnet
        self.agent.start(self.config1)

        super().on_start(ten_env)

    def on_stop(self, ten_env: TenEnv) -> None:
        ten_env.log_info("on_stop")
        self.agent.stop()
        super().on_stop(ten_env)

    def on_deinit(self, ten_env: TenEnv) -> None:
        ten_env.log_info("on_deinit")
        super().on_deinit(ten_env)

    def on_cmd(self, ten_env: TenEnv, cmd: Cmd) -> None:
        cmd_name = cmd.get_name()
        ten_env.log_info(f"on_cmd name: {cmd_name}")

        if cmd_name == CMD_IN_FLUSH:
            self.agent.flush()
            ten_env.send_cmd(Cmd.create(CMD_OUT_FLUSH))
            ten_env.log_info("on_cmd sent flush")
            status_code, detail = StatusCode.OK, "success"
            cmd_result = CmdResult.create(status_code)
            cmd_result.set_property_string("detail", detail)
            ten_env.return_result(cmd_result, cmd)
        elif cmd_name == CMD_IN_ON_USER_JOINED:
            self.users_count += 1
            # Send greeting when first user joined
            if self.greetings and self.users_count == 1:
                self.send_text_output(self.greetings, True)

            status_code, detail = StatusCode.OK, "success"
            cmd_result = CmdResult.create(status_code)
            cmd_result.set_property_string("detail", detail)
            ten_env.return_result(cmd_result, cmd)
        elif cmd_name == CMD_IN_ON_USER_LEFT:
            self.users_count -= 1
            status_code, detail = StatusCode.OK, "success"
            cmd_result = CmdResult.create(status_code)
            cmd_result.set_property_string("detail", detail)
            ten_env.return_result(cmd_result, cmd)
        else:
            super().on_cmd(ten_env, cmd)

    def on_data(self, ten_env: TenEnv, data: Data) -> None:
        data_name = data.get_name()
        ten_env.log_info("on_data name {}".format(data_name))

        # Get the necessary properties
        is_final = data.get_property_bool("is_final")
        input_text = data.get_property_string("text")

        if not is_final:
            ten_env.log_debug("ignore non-final input")
            return
        if not input_text:
            ten_env.log_warn("ignore empty text")
            return

        ten_env.log_info(f"OnData input text: [{input_text}]")
        self.agent.input_text(input_text)

    def send_text_output(
        self, sentence: str, end_of_segment: bool
    ):
        try:
            output_data = Data.create(DATA_OUT_NAME)
            output_data.set_property_string(DATA_OUT_PROPERTY_TEXT, sentence)
            output_data.set_property_bool(
                DATA_OUT_PROPERTY_END_OF_SEGMENT, end_of_segment
            )
            self.ten_env.send_data(output_data)
            self.ten_env.log_info(
                f"{'end of segment ' if end_of_segment else ''}sent sentence [{sentence}]"
            )
        except Exception as err:
            self.ten_env.log_warn(
                f"send sentence [{sentence}] failed, err: {err}")
