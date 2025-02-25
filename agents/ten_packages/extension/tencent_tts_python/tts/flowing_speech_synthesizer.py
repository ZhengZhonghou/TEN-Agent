# -*- coding: utf-8 -*-
import sys
import hmac
import hashlib
import base64
import time
import json
import threading
import websocket
import uuid
import urllib
import logging

# logging
FORMAT = '%(asctime)15s %(name)s-%(levelname)s  %(funcName)s:%(lineno)s %(message)s'
logging.basicConfig(level=logging.INFO, format=FORMAT)
logger = logging.getLogger('tencent_tts')

_PROTOCOL = "wss://"
_HOST = "tts.cloud.tencent.com"
_PATH = "/stream_wsv2"
_ACTION = "TextToStreamAudioWSv2"


class FlowingSpeechSynthesisListener(object):
    '''
    '''
    def on_synthesis_start(self, session_id):
        logger.info("on_synthesis_start: session_id={}".format(session_id))

    def on_synthesis_end(self):
        logger.info("on_synthesis_end: -")

    def on_audio_result(self, audio_bytes):
        logger.debug("on_audio_result: recv audio bytes, len={}".format(len(audio_bytes)))

    def on_text_result(self, response):
        session_id = response["session_id"]
        request_id = response["request_id"]
        message_id = response["message_id"]
        result = response['result']
        subtitles = []
        if "subtitles" in result and len(result["subtitles"]) > 0:
            subtitles = result["subtitles"]
        logger.info("on_text_result: session_id={} request_id={} message_id={}\nsubtitles={}".format(
            session_id, request_id, message_id, subtitles))

    def on_synthesis_fail(self, response):
        logger.error("on_synthesis_fail: code={} msg={}".format(
            response['code'], response['message']
        ))


NOTOPEN = 0
STARTED = 1
OPENED = 2
FINAL = 3
ERROR = 4
CLOSED = 5

FlowingSpeechSynthesizer_ACTION_SYNTHESIS = "ACTION_SYNTHESIS"
FlowingSpeechSynthesizer_ACTION_COMPLETE = "ACTION_COMPLETE"


class FlowingSpeechSynthesizer:
    '''
    response: 文本结果，类型 dict，如下
    字段名       类型         说明
    code        int         错误码（无需处理，FlowingSpeechSynthesizer中已解析，错误消息路由至 on_synthesis_fail）
    message     string      错误信息
    session_id  string      回显客户端传入的 session id
    request_id  string      请求 id，区分不同合成请求，一次 websocket 通信中，该字段相同
    message_id  string      消息 id，区分不同 websocket 消息
    final       bool        合成是否完成（无需处理，FlowingSpeechSynthesizer中已解析）
    result      Result      文本结果结构体

    Result 结构体
        字段名       类型                说明
        subtitles   array of Subtitle  时间戳数组
        
        Subtitle 结构体
        字段名       类型     说明
        Text        string  合成文本
        BeginTime   int     开始时间戳
        EndTime     int     结束时间戳
        BeginIndex  int     开始索引
        EndIndex    int     结束索引
        Phoneme     string  音素
    '''

    def __init__(self, appid, credential, listener):
        self.appid = appid
        self.credential = credential
        self.status = NOTOPEN
        self.ws = None
        self.wst = None
        self.listener = listener

        self.ready = False

        self.voice_type = 0
        self.codec = "pcm"
        self.sample_rate = 16000
        self.volume = 10
        self.speed = 0
        self.session_id = ""
        self.enable_subtitle = 0
        self.emotion_category = ""
        self.emotion_intensity = 100

    def set_voice_type(self, voice_type):
        self.voice_type = voice_type

    def set_emotion_category(self, emotion_category):
        self.emotion_category = emotion_category

    def set_emotion_intensity(self, emotion_intensity):
        self.emotion_intensity = emotion_intensity

    def set_codec(self, codec):
        self.codec = codec

    def set_sample_rate(self, sample_rate):
        self.sample_rate = sample_rate

    def set_speed(self, speed):
        self.speed = speed

    def set_volume(self, volume):
        self.volume = volume

    def set_enable_subtitle(self, enable_subtitle):
        self.enable_subtitle = enable_subtitle

    def __gen_signature(self, params):
        sort_dict = sorted(params.keys())
        sign_str = "GET" + _HOST + _PATH + "?"
        for key in sort_dict:
            sign_str = sign_str + key + "=" + str(params[key]) + '&'
        sign_str = sign_str[:-1]
        secret_key = self.credential.secret_key.encode('utf-8')
        sign_str = sign_str.encode('utf-8')
        hmacstr = hmac.new(secret_key, sign_str, hashlib.sha1).digest()
        s = base64.b64encode(hmacstr)
        s = s.decode('utf-8')
        return s

    def __gen_params(self, session_id):
        self.session_id = session_id

        params = dict()
        params['Action'] = _ACTION
        params['AppId'] = int(self.appid)
        params['SecretId'] = self.credential.secret_id
        params['ModelType'] = 1
        params['VoiceType'] = self.voice_type
        params['Codec'] = self.codec
        params['SampleRate'] = self.sample_rate
        params['Speed'] = self.speed
        params['Volume'] = self.volume
        params['SessionId'] = self.session_id
        params['EnableSubtitle'] = self.enable_subtitle
        if self.emotion_category != "":
            params['EmotionCategory']= self.emotion_category
            params['EmotionIntensity']= self.emotion_intensity

        timestamp = int(time.time())
        params['Timestamp'] = timestamp
        params['Expired'] = timestamp + 24 * 60 * 60
        return params

    def __create_query_string(self, param):
        param = sorted(param.items(), key=lambda d: d[0])

        url = _PROTOCOL + _HOST + _PATH

        signstr = url + "?"
        for x in param:
            tmp = x
            for t in tmp:
                signstr += str(t)
                signstr += "="
            signstr = signstr[:-1]
            signstr += "&"
        signstr = signstr[:-1]
        return signstr

    def __new_ws_request_message(self, action, data):
        return {
            "session_id": self.session_id,
            "message_id": str(uuid.uuid1()),

            "action": action,
            "data": data,
        }
    
    def __do_send(self, action, text):
        WSRequestMessage = self.__new_ws_request_message(action, text)
        data = json.dumps(WSRequestMessage)
        opcode = websocket.ABNF.OPCODE_TEXT
        logger.info("ws send opcode={} data={}".format(opcode, data))
        self.ws.send(data, opcode)

    def process(self, text, action=FlowingSpeechSynthesizer_ACTION_SYNTHESIS):
        logger.info("process: action={} text={}".format(action, text))
        self.__do_send(action, text)

    def complete(self, action = FlowingSpeechSynthesizer_ACTION_COMPLETE):
        logger.info("complete: action={}".format(action))
        self.__do_send(action, "")

    def wait_ready(self, timeout_ms):
        timeout_start = int(time.time() * 1000)
        while True:
            if self.ready:
                return True
            if int(time.time() * 1000) - timeout_start > timeout_ms:
                break
            time.sleep(0.01)
        return False

    def start(self):
        logger.info("synthesizer start: begin")

        def _close_conn(reason):
            ta = time.time()
            self.ws.close()
            tb = time.time()
            logger.info("client has closed connection ({}), cost {} ms".format(reason, int((tb-ta)*1000)))

        def _on_data(ws, data, opcode, flag):
            logger.debug("data={} opcode={} flag={}".format(data, opcode, flag))
            if opcode == websocket.ABNF.OPCODE_BINARY:
                self.listener.on_audio_result(data) # <class 'bytes'>
                pass
            elif opcode == websocket.ABNF.OPCODE_TEXT:
                resp = json.loads(data) # WSResponseMessage
                if resp['code'] != 0:
                    logger.error("server synthesis fail request_id={} code={} msg={}".format(
                        resp['request_id'], resp['code'], resp['message']
                    ))
                    self.listener.on_synthesis_fail(resp)
                    return
                if "final" in resp and resp['final'] == 1:
                    logger.info("recv FINAL frame")
                    self.status = FINAL
                    _close_conn("after recv final")
                    self.listener.on_synthesis_end()
                    return
                if "ready" in resp and resp['ready'] == 1:
                    logger.info("recv READY frame")
                    self.ready = True
                    return
                if "heartbeat" in resp and resp['heartbeat'] == 1:
                    logger.info("recv HEARTBEAT frame")
                    return
                if "result" in resp:
                    if "subtitles" in resp["result"] and resp["result"]["subtitles"] is not None:
                        self.listener.on_text_result(resp)
                    return
            else:
                logger.error("invalid on_data code, opcode=".format(opcode))

        def _on_error(ws, error):
            if self.status == FINAL or self.status == CLOSED:
                return
            self.status = ERROR
            logger.error("error={}, session_id={}".format(error, self.session_id))
            _close_conn("after recv error")

        def _on_close(ws, close_status_code, close_msg):
            logger.info("conn closed, close_status_code={} close_msg={}".format(close_status_code, close_msg))
            self.status = CLOSED

        def _on_open(ws):
            logger.info("conn opened")
            self.status = OPENED
            
        session_id = str(uuid.uuid1())
        params = self.__gen_params(session_id)
        signature = self.__gen_signature(params)
        requrl = self.__create_query_string(params)

        autho = urllib.parse.quote(signature)
        requrl += "&Signature=%s" % autho

        self.ws = websocket.WebSocketApp(requrl, None,# header=headers,
            on_error=_on_error, on_close=_on_close,
            on_data=_on_data)
        self.ws.on_open = _on_open

        self.status = STARTED
        self.wst = threading.Thread(target=self.ws.run_forever)
        self.wst.daemon = True
        self.wst.start()
        self.listener.on_synthesis_start(session_id)
        
        logger.info("synthesizer start: end")

    def stop(self):
        if self.ws:
            self.ws.close()
            if self.wst and self.wst.is_alive():
                self.wst.join()
