import debugpy
import dearpygui.dearpygui as dpg
import requests
import json
import threading
import time
import uuid

# Global state
is_auto_running = False
response_data = ""
interval = 10.0
CHANNEL_NAME = "flat_test_gui"
request_uuid = ""
APP_ID = "aab8b8f5a8cd4469a63042fcfafe7063"
# singapore http://43.134.185.226:8082
# a100 http://106.13.114.185:9080
# h100 http://38.102.65.36:9081

def send_start_request():
    global response_data, request_uuid
    try:
        request_uuid = uuid.uuid4()
        url = dpg.get_value("url_input") + "/start"
        app_id = dpg.get_value("appid_input")
        token = app_id
        channel_name = dpg.get_value("channelname_input")
        user_id = dpg.get_value("userid_input")
        bot_id = dpg.get_value("botid_input")
        #data = {"request_id":str(request_uuid), "channel_name":channel_name, "user_uid":user_id, "bot_id":bot_id, "app_id":app_id, "token":token}
        data = {"request_id":str(request_uuid), "channel_name":channel_name, "user_uid":user_id, "bot_id":bot_id, "app_id":app_id, "token":token}
        header = {"Content-Type":"application/json"}
        print(json.dumps(data))

        response = requests.post(
            url=url,
            headers=header,
            json=data
        )

        # Format response
        response_info = "start\n"
        response_info += json.dumps(data) + "\n\n"
        response_info += f"Status Code: {response.status_code}\n"
        #response_info += "Headers:\n"
        #response_info += json.dumps(dict(response.headers), indent=2) + "\n\n"
        response_info += "Body:\n"
        try:
            response_info += json.dumps(response.json(), indent=2)
        except:
            response_info += response.text

        response_data = response_info
        dpg.set_value("response_output", response_data)

    except Exception as e:
        dpg.set_value("response_output", f"Error: {str(e)}")

def send_stop_request():
    global response_data, request_uuid
    try:
        url = dpg.get_value("url_input") + "/stop"
        channel_name = dpg.get_value("channelname_input")
        user_id = dpg.get_value("userid_input")
        data = {"request_id":str(request_uuid), "channel_name":channel_name, "user_uid":user_id}
        header = {"Content-Type":"application/json"}
        print(json.dumps(data))

        response = requests.post(
            url=url,
            headers=header,
            json=data
        )

        # Format response
        response_info = "stop\n"
        response_info += json.dumps(data) + "\n\n"
        response_info += f"Status Code: {response.status_code}\n"
        #response_info += "Headers:\n"
        #response_info += json.dumps(dict(response.headers), indent=2) + "\n\n"
        response_info += "Body:\n"
        try:
            response_info += json.dumps(response.json(), indent=2)
        except:
            response_info += response.text

        dpg.set_value("response_output", response_info)

    except Exception as e:
        dpg.set_value("response_output", f"Error: {str(e)}")


def send_ping_request():
    global response_data, request_uuid
    try:
        url = dpg.get_value("url_input") + "/ping"
        channel_name = dpg.get_value("channelname_input")
        data = {"request_id":str(request_uuid), "channel_name":channel_name}
        header = {"Content-Type":"application/json"}
        print(json.dumps(data))

        response = requests.post(
            url=url,
            headers=header,
            json=data
        )

        # Format response
        response_info = "ping\n"
        response_info += json.dumps(data) + "\n\n"
        response_info += f"Status Code: {response.status_code}\n"
        #response_info += "Headers:\n"
        #response_info += json.dumps(dict(response.headers), indent=2) + "\n\n"
        response_info += "Body:\n"
        try:
            response_info += json.dumps(response.json(), indent=2)
        except:
            response_info += response.text

        dpg.set_value("response_output", response_info)

    except Exception as e:
        dpg.set_value("response_output", f"Error: {str(e)}")

def auto_send_loop():
    global is_auto_running
    while is_auto_running:
        send_ping_request()
        time.sleep(interval)
    send_stop_request()

def toggle_start():
    global is_auto_running, interval
    interval = float(dpg.get_value("interval_input"))
    is_auto_running = True
    dpg.disable_item("start_btn")
    dpg.enable_item("stop_btn")

    send_start_request()
    time.sleep(interval)
    threading.Thread(target=auto_send_loop, daemon=True).start()


def toggle_stop():
    global is_auto_running
    is_auto_running = False
    dpg.enable_item("start_btn")
    dpg.disable_item("stop_btn")

def create_window():
    with dpg.window(label="API Client", width=800, height=600):
        # Input Section
        with dpg.group(horizontal=True):
            dpg.add_text("URL:")
            dpg.add_input_text(width=400, tag="url_input")

        with dpg.group(horizontal=True):
            dpg.add_text("APP ID")
            dpg.add_input_text(width=400, default_value=APP_ID, tag="appid_input")
        with dpg.group(horizontal=True):
            dpg.add_text("Channel Name")
            dpg.add_input_text(width=400, default_value=CHANNEL_NAME, tag="channelname_input")
        with dpg.group(horizontal=True):
            dpg.add_text("user id")
            dpg.add_input_int(width=200, default_value=123, step=0, step_fast=0, tag="userid_input")
            dpg.add_text("bot id")
            dpg.add_input_int(width=200, default_value=1234, step=0, step_fast=0, tag="botid_input")
            
        # Controls
        with dpg.group(horizontal=True):
            dpg.add_button(width=100, label="Start", tag="start_btn", callback=toggle_start)
            dpg.add_button(width=100, label="Stop", tag="stop_btn", callback=toggle_stop, enabled=False)
            dpg.add_input_float(
                label="Interval (s)",
                default_value=interval,
                width=100,
                tag="interval_input",
                min_value=1.0,
                min_clamped=True
            )
            
        # Response Display
        dpg.add_separator()
        dpg.add_text("Response")
        dpg.add_input_text(
            multiline=True,
            readonly=True,
            width=760,
            height=300,
            tag="response_output",
            label=""
        )

def main():
    dpg.create_context()
    dpg.create_viewport(title="API Client - Dear PyGui", width=800, height=600)
    
    create_window()
    
    dpg.setup_dearpygui()
    dpg.show_viewport()
    dpg.start_dearpygui()
    dpg.destroy_context()

if __name__ == "__main__":
    main()
