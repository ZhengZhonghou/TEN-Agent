#
#
# Agora Real Time Engagement
# Created by Wei Hu in 2024-08.
# Copyright (c) 2024 Agora IO. All rights reserved.
#
#
from collections import defaultdict
from dataclasses import dataclass
import random
import requests
import time
from openai import OpenAI
from openai.types.chat.chat_completion import ChatCompletion
from .logging import logger
from .helper import parse_sentences


class OpenAIChatGPTCallbackBase():
    def __init__(self):
        pass

    def tool_call(self, tool):
        pass

    def output_sentence(self, sentence:str, end:bool):
        pass

    def emit_signal(self, signal:str, content:str):
        pass


@dataclass
class OpenAIChatGPTConfig():
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = (
        "gpt-4o"  # Adjust this to match the equivalent of `openai.GPT4o` in the Python library
    )
    prompt: str = (
        "You are a voice assistant who talks in a conversational way and can chat with me like my friends. I will speak to you in English or Chinese, and you will answer in the corrected and improved version of my text with the language I use. Don’t talk like a robot, instead I would like you to talk like a real human with emotions. I will use your answer for text-to-speech, so don’t return me any meaningless characters. I want you to be helpful, when I’m asking you for advice, give me precise, practical and useful advice instead of being vague. When giving me a list of options, express the options in a narrative way instead of bullet points. Be brief (1-2 sentences) unless detailed explanation needed by user."
    )
    frequency_penalty: float = 0.9
    presence_penalty: float = 0.9
    top_p: float = 1.0
    temperature: float = 0.1
    max_tokens: int = 512
    seed: int = random.randint(0, 10000)
    proxy_url: str = ""
    max_memory_length: int = 10
    vendor: str = "openai"
    azure_endpoint: str = ""
    azure_api_version: str = ""

class ThinkParser:
    def __init__(self):
        self.state = 'NORMAL'  # States: 'NORMAL', 'THINK'
        self.think_content = ""
        self.content = ""
    
    def process(self, new_chars):
        if new_chars == "<think>":
            self.state = 'THINK'
            return True
        elif new_chars == "</think>":
            self.state = 'NORMAL'
            return True
        else:
            if self.state == "THINK":
                self.think_content += new_chars
        return False
        

class OpenAIChatGPT:
    client = None

    def __init__(self, config: OpenAIChatGPTConfig):
        self.config = config
        logger.info(f"OpenAIChatGPT initialized with config: {config.api_key}")
        self.client = OpenAI(api_key=config.api_key, base_url=config.base_url, default_headers={
            "api-key": config.api_key,
        })
        self.session = requests.Session()
        if config.proxy_url:
            proxies = {
                "http": config.proxy_url,
                "https": config.proxy_url,
            }
            logger.info(f"Setting proxies: {proxies}")
            self.session.proxies.update(proxies)
        self.client.session = self.session

    def get_chat_completions(self, messages, tools=None) -> ChatCompletion:
        req = {
            "model": self.config.model,
            "messages": [
                {
                    "role": "system",
                    "content": self.config.prompt,
                },
                *messages,
            ],
            "tools": tools,
            "temperature": self.config.temperature,
            "top_p": self.config.top_p,
            "presence_penalty": self.config.presence_penalty,
            "frequency_penalty": self.config.frequency_penalty,
            "max_tokens": self.config.max_tokens,
            "seed": self.config.seed,
        }

        try:
            response = self.client.chat.completions.create(**req)
        except Exception as e:
            raise RuntimeError(f"CreateChatCompletion failed, err: {e}") from e

        return response

    def get_chat_completions_stream(self, messages, tools=None, listener: OpenAIChatGPTCallbackBase=None):

        curr_task_start_ts = time.time()
        curr_task_ttfb = None
        curr_task_ttfs = None

        req = {
            "model": self.config.model,
            "messages": [
                {
                    "role": "system",
                    "content": self.config.prompt,
                },
                *messages,
            ],
            "tools": tools,
            "temperature": self.config.temperature,
            "top_p": self.config.top_p,
            "presence_penalty": self.config.presence_penalty,
            "frequency_penalty": self.config.frequency_penalty,
            "max_tokens": self.config.max_tokens,
            "seed": self.config.seed,
            "stream": True,
        }

        try:
            response = self.client.chat.completions.create(**req)
        except Exception as e:
            raise RuntimeError(f"CreateChatCompletionStream failed, err: {e}") from e

        full_content = ""
        # Check for tool calls
        tool_calls_dict = defaultdict(
            lambda: {
                "id": None,
                "function": {"arguments": "", "name": None},
                "type": None,
            }
        )

        # Example usage
        parser = ThinkParser()

        sentence_fragment = ""
        for chat_completion in response:
            if len(chat_completion.choices) == 0:
                continue
            choice = chat_completion.choices[0]
            delta = choice.delta

            content = delta.content if delta and delta.content else ""

            if curr_task_ttfb is None:
                curr_task_ttfb = self._duration_in_ms(curr_task_start_ts, time.time())

            # Emit content update event (fire-and-forget)
            if listener and content:
                prev_state = parser.state
                is_special_char = parser.process(content)

                if not is_special_char:
                    # logger.info(f"state: {parser.state}, content: {content}, think: {parser.think_content}")
                    if parser.state == "THINK":
                        listener.emit_signal("reasoning_update", parser.think_content)
                    elif parser.state == "NORMAL":
                        listener.emit_signal("content_update", content)

                if prev_state == "THINK" and parser.state == "NORMAL":
                    listener.emit_signal("reasoning_update_finish", parser.think_content)
                    parser.think_content = ""

            full_content += content

            sentences, sentence_fragment = parse_sentences(
                    sentence_fragment, content
            )
            if len(sentences) > 0 and curr_task_ttfs is None:
                curr_task_ttfs = self._duration_in_ms(curr_task_start_ts, time.time())
                logger.info(f"ChatGPT FFTB: {curr_task_ttfb}ms, FFTS: {curr_task_ttfs}ms")

            for s in sentences:
                yield s

            if delta.tool_calls:
                for tool_call in delta.tool_calls:
                    if tool_call.id is not None:
                        tool_calls_dict[tool_call.index]["id"] = tool_call.id

                    # If the function name is not None, set it
                    if tool_call.function.name is not None:
                        tool_calls_dict[tool_call.index]["function"][
                            "name"
                        ] = tool_call.function.name

                    # Append the arguments
                    tool_calls_dict[tool_call.index]["function"][
                        "arguments"
                    ] += tool_call.function.arguments

                    # If the type is not None, set it
                    if tool_call.type is not None:
                        tool_calls_dict[tool_call.index]["type"] = tool_call.type

        # Convert the dictionary to a list
        tool_calls_list = list(tool_calls_dict.values())

        # Emit tool calls event (fire-and-forget)
        if listener and tool_calls_list:
            for tool_call in tool_calls_list:
                listener.tool_call(tool_call)

        # Emit content finished event after the loop completes
        if listener:
            listener.emit_signal("finish", full_content)

    def _duration_in_ms(self, start, end) -> int:
        return int((end - start) * 1000)
