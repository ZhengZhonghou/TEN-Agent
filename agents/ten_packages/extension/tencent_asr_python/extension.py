from ten import (
    Extension,
    TenEnv,
    Cmd,
    Data,
    AudioFrame,
    StatusCode,
    CmdResult,
)

import time
import json
import os

from .asr import speech_recognizer

DATA_OUT_TEXT_DATA_PROPERTY_TEXT = "text"
DATA_OUT_TEXT_DATA_PROPERTY_IS_FINAL = "is_final"
DATA_OUT_TEXT_DATA_PROPERTY_STREAM_ID = "stream_id"
DATA_OUT_TEXT_DATA_PROPERTY_END_OF_SEGMENT = "end_of_segment"

class Credential:
    def __init__(self, secret_id, secret_key, token=""):
        self.secret_id = secret_id
        self.secret_key = secret_key
        self.token = token

class MySpeechRecognitionListener(speech_recognizer.SpeechRecognitionListener):
    def __init__(self, sender):
        self.sender = sender
        self.ten_env = sender.ten_env

    def on_recognition_start(self, response):
        self.ten_env.log_info(f"OnRecognitionStart: {response['voice_id']}")
        #print("%s|%s|OnRecognitionStart\n" % (time.ctime(), response['voice_id']))

    def on_sentence_begin(self, response):
        rsp_str = json.dumps(response, ensure_ascii=False)
        self.ten_env.log_info(f"OnRecognitionSentenceBegin: {response['voice_id']}, rsp: {rsp_str}")
        #print("%s|%s|OnRecognitionSentenceBegin, rsp %s\n" % (time.ctime(), response['voice_id'], rsp_str))

    def on_recognition_result_change(self, response):
        pass
        #rsp_str = json.dumps(response, ensure_ascii=False)
        #self.ten_env.log_info(f"OnResultChange: {response['voice_id']}, rsp: {rsp_str}")
        #print("%s|%s|OnResultChange, rsp %s\n" % (time.ctime(), response['voice_id'], rsp_str))

    def on_sentence_end(self, response):
        rsp_str = json.dumps(response, ensure_ascii=False)
        self.ten_env.log_info(f"OnSentenceEnd: {response['voice_id']}, rsp: {rsp_str}")
        self.sender._send_text(response['result']['voice_text_str'], True, self.sender.stream_id)
        #print("%s|%s|OnSentenceEnd, rsp %s\n" % (time.ctime(), response['voice_id'], rsp_str))

    def on_recognition_complete(self, response):
        self.ten_env.log_info(f"OnRecognitionComplete: {response['voice_id']}")
        print("%s|%s|OnRecognitionComplete\n" % (time.ctime(), response['voice_id']))

    def on_fail(self, response):
        rsp_str = json.dumps(response, ensure_ascii=False)
        self.ten_env.log_info(f"OnRecognitionComplete: {response['voice_id']}")
        #print("%s|%s|OnFail,message %s\n" % (time.ctime(), response['voice_id'], rsp_str))


class TencentASRExtension(Extension):
    def __init__(self, name: str):
        super().__init__(name)

        self.app_id = 0
        self.secret_id = ""
        self.secret_key = ""
        self.model = "16k_en"
        if "TENCENT_APPID" in os.environ:
            self.app_id = os.environ["TENCENT_APPID"]
        if "TENCENT_SECRETID" in os.environ:
            self.secret_id = os.environ["TENCENT_SECRETID"]
        if "TENCENT_SECRETKEY" in os.environ:
            self.secret_key = os.environ["TENCENT_SECRETKEY"]

        self.connected = False
        self.ten_env: TenEnv = None
        self.stream_id = -1

    def on_init(self, ten_env: TenEnv) -> None:
        ten_env.log_info("TencentASRExtension on_init")
        try:
            self.model = ten_env.get_property_string("model")
        except Exception as err:
            ten_env.log_warn(f"Error reading model property: {err}")

        ten_env.on_init_done()

    def on_start(self, ten_env: TenEnv) -> None:
        ten_env.log_info("on_start")
        self.ten_env = ten_env

        self.listener = MySpeechRecognitionListener(self)
        credential_var = Credential(self.secret_id, self.secret_key)
        self.recognizer = speech_recognizer.SpeechRecognizer(
            self.app_id, credential_var, self.model, self.listener)
        self.recognizer.set_voice_format(1)
        self.recognizer.set_word_info(0)
        self.recognizer.set_need_vad(1)
        self.recognizer.set_vad_silence_time(500)
        self.recognizer.set_convert_num_mode(1)

        #if not self.config.api_key:
        #    ten_env.log_error("get property api_key")
        #    return

        ten_env.on_start_done()

    def on_audio_frame(self, _: TenEnv, frame: AudioFrame) -> None:
        if not self.connected:
            return

        frame_buf = frame.get_buf()

        if not frame_buf:
            self.ten_env.log_warn("send_frame: empty pcm_frame detected.")
            return

        self.stream_id = frame.get_property_int("stream_id")
        self.recognizer.write(frame_buf)

    def on_stop(self, ten_env: TenEnv) -> None:
        if self.connected:
            self.connected = False
            time.sleep(0.1)
            self.recognizer.stop()
        ten_env.log_info("on_stop")
        ten_env.on_stop_done()

    def on_cmd(self, ten_env: TenEnv, cmd: Cmd) -> None:
        cmd_name = cmd.get_name()
        if cmd_name == "on_user_joined":
            remote_id = cmd.get_property_string("remote_user_id")
            ten_env.log_info(f"on_user_joined: uid: {remote_id}")
            self.recognizer.start()
            time.sleep(0.1)
            self.connected = True
        if cmd_name == "on_user_left":
            remote_id = cmd.get_property_string("remote_user_id")
            ten_env.log_info(f"on_user_left: uid: {remote_id}")
            self.connected = False
            time.sleep(0.1)
            self.recognizer.stop()

        cmd_result = CmdResult.create(StatusCode.OK)
        cmd_result.set_property_string("detail", "success")
        ten_env.return_result(cmd_result, cmd)


    def _send_text(self, text: str, is_final: bool, stream_id: str) -> None:
        stable_data = Data.create("text_data")
        stable_data.set_property_bool(DATA_OUT_TEXT_DATA_PROPERTY_IS_FINAL, is_final)
        stable_data.set_property_string(DATA_OUT_TEXT_DATA_PROPERTY_TEXT, text)
        stable_data.set_property_int(DATA_OUT_TEXT_DATA_PROPERTY_STREAM_ID, stream_id)
        stable_data.set_property_bool(
            DATA_OUT_TEXT_DATA_PROPERTY_END_OF_SEGMENT, is_final
        )
        self.ten_env.log_info(f"sending text: {text}")
        self.ten_env.send_data(stable_data)
