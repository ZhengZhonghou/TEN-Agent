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
import queue, threading
import os

from .tts import flowing_speech_synthesizer

class Credential:
    def __init__(self, secret_id, secret_key, token=""):
        self.secret_id = secret_id
        self.secret_key = secret_key
        self.token = token


class MySpeechSynthesisListener(flowing_speech_synthesizer.FlowingSpeechSynthesisListener):

    def __init__(self, sender):
        self.sender = sender
        self.ten_env = sender.ten_env

        self.codec = "pcm"
        self.sample_rate = 16000

        self.audio_file = ""
        self.pcm_fp = None
    
    def set_audio_file(self, filename):
        self.audio_file = filename

    def on_synthesis_start(self, session_id):
        super().on_synthesis_start(session_id)
        if self.audio_file != "":
            self.pcm_fp = open(self.audio_file, "wb")
        self.ten_env.log_info("synthesis start")

    def on_synthesis_end(self):
        super().on_synthesis_end()
        if self.pcm_fp:
            self.pcm_fp.close()
        self.ten_env.log_info("synthesis end")

    def on_audio_result(self, audio_bytes):
        # super().on_audio_result(audio_bytes)
        if self.pcm_fp:
            self.pcm_fp.write(audio_bytes)
        self.sender.send_audio_data(audio_bytes)

    def on_text_result(self, response):
        super().on_text_result(response)
        result = response["result"]

    def on_synthesis_fail(self, response):
        super().on_synthesis_fail(response)
        err_code = response["code"]
        err_msg = response["message"]
        self.ten_env.log_warn(f"tts synthesis failed. code: {err_code}, {err_msg}")


class TencentTTSExtension(Extension):
    def __init__(self, name: str):
        super().__init__(name)

        self.app_id = 0
        self.secret_id = ""
        self.secret_key = ""
        self.sample_rate = 16000
        self.voice_id = 101051
        if "TENCENT_APPID" in os.environ:
            self.app_id = os.environ["TENCENT_APPID"]
        if "TENCENT_SECRETID" in os.environ:
            self.secret_id = os.environ["TENCENT_SECRETID"]
        if "TENCENT_SECRETKEY" in os.environ:
            self.secret_key = os.environ["TENCENT_SECRETKEY"]

        self.connected = False
        self.ten_env: TenEnv = None
        self.thread = None
        self.queue = queue.Queue()

        self.outdate_ts = time.time()
        self.outdate_ts_lock = threading.Lock()

        self.start_time = time.time()
        self.curr_task_recv_ts = None
        self.ttfb = None
        self.ttfb_start_ts = None


    def on_init(self, ten_env: TenEnv) -> None:
        ten_env.log_info("TencentTTSExtension on_init")
        try:
            self.voice_id = ten_env.get_property_string("voice_id")
        except Exception as err:
            ten_env.log_warn(f"Error reading voice_id property: {err}")

        ten_env.on_init_done()

    def on_start(self, ten_env: TenEnv) -> None:
        ten_env.log_info("on_start")
        self.ten_env = ten_env

        self.listener = MySpeechSynthesisListener(self)
        credential_var = Credential(self.secret_id, self.secret_key)
        self.synthesizer = flowing_speech_synthesizer.FlowingSpeechSynthesizer(
                    self.app_id, credential_var, self.listener)
        self.synthesizer.set_voice_type(self.voice_id)
        self.synthesizer.set_codec("pcm")
        self.synthesizer.set_sample_rate(self.sample_rate)
        self.synthesizer.set_enable_subtitle(False)

        #if not self.config.api_key:
        #    ten_env.log_error("get property api_key")
        #    return

        self.start_ts = time.time()
        self.next_ts = 0
        self.thread = threading.Thread(target=self._async_handle, args=[ten_env])
        self.thread.start()

        ten_env.on_start_done()

    def on_stop(self, ten_env: TenEnv) -> None:
        ten_env.log_info("on_stop")
        self._flush()
        self.queue.put(None)
        if self.thread:
            self.thread.join()
            self.thread = None
        if self.connected:
            self.connected = False
            self.synthesizer.complete()
            self.synthesizer.stop()

        self.ten = None
        ten_env.on_stop_done()

    def on_cmd(self, ten_env: TenEnv, cmd: Cmd) -> None:
        cmd_name = cmd.get_name()
        if cmd_name == "on_user_joined":
            remote_id = cmd.get_property_string("remote_user_id")
            ten_env.log_info(f"on_user_joined: uid: {remote_id}")
            self.synthesizer.start()
            ready = self.synthesizer.wait_ready(5000)
            if not ready:
                ten_env.log_error("Unable to start tts")
            self.connected = True
        if cmd_name == "on_user_left":
            remote_id = cmd.get_property_string("remote_user_id")
            ten_env.log_info(f"on_user_left: uid: {remote_id}")
            self._flush()
            self.connected = False
            self.synthesizer.complete()
            self.synthesizer.stop()
        if cmd_name == "flush":
            ten_env.log_info("cmd flush received")
            self._flush()
            self.ttfb = None
            self.ttfb_start_ts = None
            # send flush to downstream
            ten_env.send_cmd(Cmd.create("flush"))

        cmd_result = CmdResult.create(StatusCode.OK)
        cmd_result.set_property_string("detail", "success")
        ten_env.return_result(cmd_result, cmd)

    def on_data(self, ten_env: TenEnv, data: Data) -> None:
        input_text = data.get_property_string("text")
        if len(input_text) == 0:
            ten_env.log_info("on_data ignore empty text")
            return

        ts = time.time()
        ten_env.log_info("on_data, text [{}] recv_ts [{}]".format(input_text, ts))
        self.queue.put((input_text, ts))


    def send_audio_data(self, audio_bytes):
        if self._outdated(self.curr_task_recv_ts):
            self.ten_env.log_warn("ignore outdated audio")
            return
        ttfb_start_ts = self.ttfb_start_ts
        if not ttfb_start_ts:
            self.ten_env.log_warn("ttfb_start_ts is None, ignore audio")
            return

        self.ten_env.log_info(
            "audio_bytes len {} ".format(len(audio_bytes))
        )
        if len(audio_bytes) == 0:
            self.ten_env.log_warn("ignore empty audio")
            return

        # calc TTFB
        if self.ttfb is None:
            self.ttfb = self._duration_in_ms_since(ttfb_start_ts)
            self.ten_env.log_info("TTS TTFB {}ms".format(self.ttfb))

            # NOTE: check whether output meets assumption from downstream
            audio_duration_in_ms = int(len(audio_bytes) / 2 * 1000 / self.sample_rate)
            EXPECT_DURATION_IN_MS = 160
            if audio_duration_in_ms < EXPECT_DURATION_IN_MS:
                self.ten_env.log_warn(
                    "first audio duration {}ms too small, expect >= {}ms".format(
                        audio_duration_in_ms, EXPECT_DURATION_IN_MS
                    ),
                )

        # compare next_ts with current timestamp
        next_ts = (time.time() - self.start_ts) * 1000
        if self.next_ts < next_ts:
            self.next_ts = next_ts
        self.ten_env.log_debug(f"send pcm_frame, timestamp {int(self.next_ts)}")

        # send out frame
        f = AudioFrame.create("pcm_frame")
        f.set_sample_rate(self.sample_rate)
        f.set_bytes_per_sample(2)
        f.set_number_of_channels(1)
        f.set_timestamp(int(self.next_ts))
        f.set_samples_per_channel(len(audio_bytes) // 2)
        f.alloc_buf(len(audio_bytes))
        buf = f.lock_buf()
        buf[:] = audio_bytes[:]
        f.unlock_buf(buf)
        self.ten_env.send_audio_frame(f)

        # adjust next_ts
        self.next_ts += len(audio_bytes) / 2 * 1000 / self.sample_rate


    def _async_handle(self, ten_env: TenEnv):
        while True:
            try:
                value = self.queue.get()
                if value is None:
                    break
                input_text, recv_ts = value

                start_ts = time.time()
                self.curr_task_recv_ts = recv_ts
                if self.ttfb_start_ts is None:
                    self.ttfb_start_ts = start_ts

                ten_env.log_info(
                    "start process text [{}] recv_ts [{}] queued_time {}ms".format(
                        input_text,
                        recv_ts,
                        self._duration_in_ms(recv_ts, start_ts),
                    )
                )

                self.synthesizer.process(input_text)
                time.sleep(0.1)

            except Exception as e:
                ten_env.log_error("unexpected exception {}".format(e))

    def _flush(self):
        with self.outdate_ts_lock:
            self.outdate_ts = time.time()
        while not self.queue.empty():
            self.queue.get()
        # adjust next_ts
        self.next_ts = (time.time() - self.start_ts) * 1000

    def _outdated(self, ts) -> bool:
        with self.outdate_ts_lock:
            return ts < self.outdate_ts

    def _duration_in_ms(self, start, end) -> int:
        return int((end - start) * 1000)

    def _duration_in_ms_since(self, start) -> int:
        return self._duration_in_ms(start, time.time())
