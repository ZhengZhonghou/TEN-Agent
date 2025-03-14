
import json
import threading
import queue
import time
from .helper import ChatMemory
from .logging import logger
from .openai import OpenAIChatGPTConfig, OpenAIChatGPT


class Agent():
    def __init__(self, sender = None):
        self.config = OpenAIChatGPTConfig()
        self.client = None
        self.last_reasoning_ts = 0
        self.chat_memory = None
        self.stopping = False
        self.thread = None
        self.queue = queue.Queue()
        self.sender = sender

    def start(self, config:OpenAIChatGPTConfig):
        self.config = config
        # Create instance
        try:
            self.client = OpenAIChatGPT(self.config)
            logger.info(
                f"initialized with max_tokens: {self.config.max_tokens}, model: {self.config.model}, url: {self.config.base_url}"
            )
        except Exception as err:
            logger.info(f"Failed to initialize OpenAIChatGPT: {err}")
        
        self.chat_memory = ChatMemory(self.config.max_memory_length)
        # start thread
        self.thread = threading.Thread(target=self.async_handle)
        self.thread.start()


    def stop(self):
        self.stopping = True
        self.flush()
        self.queue.put(None)
        if self.thread is not None:
            self.thread.join()
            self.thread = None
        self.client = None

    def async_handle(self):
        while not self.stopping:
            try:
                value = self.queue.get()
                if value is None or self.stopping:
                    break
                input_text, ts = value

                # prepare messages
                self.chat_memory.put({"role": "user", "content": input_text})
                messages = self.chat_memory.get()

                # chat
                logger.info(
                    f"start processing input text [{input_text}] task_recv_ts [{ts}] task_start_ts [{time.time()}] len(messages) = {len(messages)}"
                )
                text_stream = self.client.get_chat_completions_stream(messages)
                full_answer = ""
                for partial_answer in text_stream:
                    full_answer += partial_answer
                    if self.sender:
                        self.sender.send_text_output(partial_answer, False)
                logger.info(f"finished processing input text [{input_text}] full answer: [{full_answer}]")
                self.chat_memory.put({"role": "assistant", "content": full_answer})
                if self.sender:
                    self.sender.send_text_output("", True)
            except Exception as e:
                logger.warning(e)

    def input_text(self, text):
        ts = time.time()
        # put into queue
        self.queue.put((text, ts))

    def flush(self):
        while not self.queue.empty():
            self.queue.get()

if __name__ == "__main__":
    agent = Agent()
