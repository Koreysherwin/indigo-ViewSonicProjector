import indigo
import socket
import threading
import time
import binascii

# Per ViewSonic table:
# Power ON  = 06 14 00 04 00 34 11 00 00 5D
# Power OFF = 06 14 00 04 00 34 11 01 00 5E
CMD_POWER_QUERY = [0x07,0x14,0x00,0x05,0x00,0x34,0x00,0x00,0x11,0x00,0x5E]
CMD_POWER_ON    = [0x06,0x14,0x00,0x04,0x00,0x34,0x11,0x00,0x00,0x5D]
CMD_POWER_OFF   = [0x06,0x14,0x00,0x04,0x00,0x34,0x11,0x01,0x00,0x5E]

# Source input commands from ViewSonic RS-232 table:
# 122 HDMI1, 123 HDMI2, 125 Source input read status
CMD_INPUT_QUERY = [0x07,0x14,0x00,0x05,0x00,0x34,0x00,0x00,0x13,0x01,0x61]
CMD_INPUT_HDMI1 = [0x06,0x14,0x00,0x04,0x00,0x34,0x13,0x01,0x03,0x63]
CMD_INPUT_HDMI2 = [0x06,0x14,0x00,0x04,0x00,0x34,0x13,0x01,0x07,0x67]

# Light source usage time commands from ViewSonic RS-232 table:
# 161 = equivalent hours, 162 = usage time
CMD_LAMP_EQ_QUERY    = [0x07,0x14,0x00,0x05,0x00,0x34,0x00,0x00,0x15,0x01,0x63]
CMD_LAMP_USAGE_QUERY = [0x07,0x14,0x00,0x05,0x00,0x34,0x00,0x00,0x15,0x0A,0x6C]

POWER_STATE_MAP = {
    0x00: "off",
    0x01: "on",
    0x02: "warming",
    0x03: "cooling",
}

INPUT_STATE_MAP = {
    0x03: "hdmi1",
    0x07: "hdmi2",
}

INPUT_LABEL_MAP = {
    "hdmi1": "HDMI 1",
    "hdmi2": "HDMI 2",
    "unknown": "Unknown",
}

LIGHT_SOURCE_MODE_MAP = {
    0x00: "standby",
    0x01: "ignition",
    0x02: "ignition",
    0x03: "ignition",
    0x04: "lamp up",
    0x05: "cool down",
    0x06: "normal operation",
    0x07: "reserved",
    0x08: "shutdown due to unrecoverable error",
    0x09: "pre-heating phase",
    0x0A: "reserved",
    0x0B: "reserved",
    0x0C: "pre-heating phase",
}

LIGHT_SOURCE_ERROR_MAP = {
    0x00: "no error",
    0x01: "temperature shutdown",
    0x02: "short circuit output detected",
    0x03: "end of lamp life detected",
    0x04: "lamp did not ignite",
}

ERROR_ITEM_NAMES = [
    "Lamp turn on fail",
    "Lamp lit error",
    "Fan1 fail (blower fan)",
    "Fan2 fail (lamp fan)",
    "Fan3 fail (power fan)",
    "Fan4 fail",
    "Thermal sensor 1 open",
    "Thermal sensor 2 open",
    "Thermal sensor 1 short",
    "Thermal sensor 2 short",
    "Thermal sensor 1 over-temp",
    "Thermal sensor 2 over-temp",
    "Fan IC 1 error",
    "Color wheel error",
    "Color wheel startup error",
    "UART1 watchdog error",
    "Abnormal powerdown",
]

class Plugin(indigo.PluginBase):

    def __init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs):
        super().__init__(pluginId, pluginDisplayName, pluginVersion, pluginPrefs)
        self._polling = {}
        self._locks = {}
        self._pause_until = {}
        self._pending_input_query_at = {}
        self._pending_lamp_query_at = {}
        self._last_lamp_query_at = {}
        self._transient_candidate = {}
        self._commanded_transition = {}
        self._load_prefs(pluginPrefs)

    def startup(self):
        indigo.server.log(f"{self.pluginDisplayName} v{self.pluginVersion} starting")

    def getDeviceStateList(self, device):
        state_list = super().getDeviceStateList(device)
        existing = {item.get("key") for item in state_list if isinstance(item, dict)}

        def add_state(key, value_type, trigger, control, readonly=False, options=None):
            if key in existing:
                return
            entry = {
                "key": key,
                "valueType": value_type,
                "triggerLabel": trigger,
                "controlPageLabel": control,
            }
            if readonly:
                entry["readonly"] = True
            if options is not None:
                entry["type"] = "menu"
                entry["options"] = [{"value": v, "label": l} for v, l in options]
            state_list.append(entry)
            existing.add(key)

        add_state("power", "String", "Power State", "Power State", options=[
            ("unknown", "Unknown"),
            ("off", "Off"),
            ("on", "On"),
            ("warming", "Warming Up"),
            ("cooling", "Cooling Down"),
        ])
        add_state("input", "String", "Current Input", "Current Input", options=[
            ("unknown", "Unknown"),
            ("hdmi1", "HDMI 1"),
            ("hdmi2", "HDMI 2"),
        ])
        add_state("readyState", "String", "System Ready", "System Ready", readonly=True, options=[
            ("false", "No"),
            ("true", "Yes"),
        ])
        add_state("rawState", "Number", "Raw Power State Byte", "Raw Power State Byte", readonly=True)
        add_state("rawInput", "Number", "Raw Input Byte", "Raw Input Byte", readonly=True)
        add_state("lastResponse", "String", "Last Response (Hex)", "Last Response (Hex)", readonly=True)
        add_state("gcAddress", "String", "GC Unit Address", "GC Unit Address", readonly=True)
        add_state("lampHours", "Number", "Light Source Usage Time (hrs)", "Light Source Usage Time (hrs)", readonly=True)
        add_state("equivalentLampHours", "Number", "Equivalent Light Source Hours", "Equivalent Light Source Hours", readonly=True)
        add_state("maintenanceDue", "String", "Maintenance Due", "Maintenance Due", readonly=True, options=[
            ("false", "No"),
            ("true", "Yes"),
        ])
        add_state("maintenanceSummary", "String", "Maintenance Summary", "Maintenance Summary", readonly=True)
        add_state("lastErrorSummary", "String", "Last Error Summary", "Last Error Summary", readonly=True)
        return state_list

    def _bool_pref(self, value):
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")

    def _load_prefs(self, prefs):
        legacy = self._bool_pref(prefs.get("showRs232Debug", False))
        self._log_tx = self._bool_pref(prefs.get("logRs232Tx", legacy))
        self._log_rx = self._bool_pref(prefs.get("logRs232Rx", legacy))
        self._verbose_decode = self._bool_pref(prefs.get("verboseProtocolDecode", False))

    def closedPrefsConfigUi(self, valuesDict, userCancelled):
        if userCancelled:
            return
        self._load_prefs(valuesDict)
        indigo.server.log(
            "RS232 logging updated: TX={}, RX={}, Decode={}".format(
                "enabled" if self._log_tx else "disabled",
                "enabled" if self._log_rx else "disabled",
                "enabled" if self._verbose_decode else "disabled",
            )
        )

    def _format_hex(self, payload):
        if payload is None:
            return "<none>"
        try:
            return " ".join(f"{b:02X}" for b in bytearray(payload))
        except Exception:
            return str(payload)

    def _decode_payload(self, payload, is_rx=False):
        try:
            data = list(bytearray(payload))
        except Exception:
            return None

        if not data:
            return "empty payload"

        if data == CMD_POWER_QUERY:
            return "Power query"
        if data == CMD_POWER_ON:
            return "Power command: ON"
        if data == CMD_POWER_OFF:
            return "Power command: OFF"
        if data == CMD_INPUT_QUERY:
            return "Input query"
        if data == CMD_INPUT_HDMI1:
            return "Input command: HDMI 1"
        if data == CMD_INPUT_HDMI2:
            return "Input command: HDMI 2"
        if data == CMD_LAMP_EQ_QUERY:
            return "Equivalent light source hours query"
        if data == CMD_LAMP_USAGE_QUERY:
            return "Light source usage time query"

        if is_rx and len(data) >= 12 and data[:4] == [0x05, 0x14, 0x00, 0x06]:
            hours = int.from_bytes(bytes(data[7:11]), byteorder="little", signed=False)
            return f"Light source hours: {hours}"

        if is_rx and len(data) >= 32 and data[:4] == [0x05, 0x14, 0x00, 0x16]:
            return self._decode_error_status_payload(data)

        if is_rx and len(data) >= 9 and data[0] == 0x05 and data[1] == 0x14 and data[5] == 0x00 and data[6] == 0x00:
            if data[7] in POWER_STATE_MAP:
                return f"Power status: {POWER_STATE_MAP[data[7]].upper()}"
            input_state = INPUT_STATE_MAP.get(data[7])
            if input_state:
                return f"Input status: {INPUT_LABEL_MAP[input_state]}"

        if is_rx and len(data) == 6 and data[:5] == [0x03, 0x14, 0x00, 0x00, 0x00]:
            return "ACK"

        return None


    def _decode_error_status_payload(self, data):
        try:
            counts = data[7:24]
            burn_in_minutes = int.from_bytes(bytes(data[24:28]), byteorder="little", signed=False)
            light_mode = data[28] if len(data) > 28 else 0
            light_error_raw = data[29] if len(data) > 29 else 0

            active = []
            for idx, count in enumerate(counts[:17]):
                if count:
                    active.append(f"{ERROR_ITEM_NAMES[idx]}={count}")

            mode_text = LIGHT_SOURCE_MODE_MAP.get(light_mode, f"0x{light_mode:02X}")
            error_text = LIGHT_SOURCE_ERROR_MAP.get(light_error_raw, f"0x{light_error_raw:02X}")

            parts = []
            if active:
                parts.append(", ".join(active[:5]))
                if len(active) > 5:
                    parts[-1] += f" (+{len(active)-5} more)"
            parts.append(f"Mode={mode_text}")
            parts.append(f"LightError={error_text}")
            if burn_in_minutes:
                parts.append(f"BurnInMin={burn_in_minutes}")
            return "Error status: " + "; ".join(parts)
        except Exception:
            return "Error status packet"

    def _get_maintenance_alert_hours(self, device):
        raw = str(device.pluginProps.get("maintenanceAlertHours", "20000")).strip()
        if raw == "":
            return 0
        try:
            return max(0, int(raw))
        except Exception:
            return 20000

    def _format_int_commas(self, value):
        try:
            return f"{int(value):,}"
        except Exception:
            return str(value)

    def _update_maintenance_due(self, device):
        threshold = self._get_maintenance_alert_hours(device)
        lamp_hours = int(device.states.get("lampHours", 0) or 0)
        eq_hours = int(device.states.get("equivalentLampHours", 0) or 0)
        display_hours = lamp_hours if lamp_hours > 0 else eq_hours
        due = threshold > 0 and max(lamp_hours, eq_hours) >= threshold
        previous = str(device.states.get("maintenanceDue", "false")).lower()
        current = "true" if due else "false"
        self._update_state_if_changed(device, "maintenanceDue", current)

        if threshold > 0:
            summary = f"Lamp: {self._format_int_commas(display_hours)} / {self._format_int_commas(threshold)} hrs"
            if due:
                summary += " (due)"
        else:
            summary = f"Lamp: {self._format_int_commas(display_hours)} hrs"

        self._update_state_if_changed(device, "maintenanceSummary", summary)

        if current == "true" and previous != "true":
            indigo.server.log(
                f"{device.name}: maintenance threshold reached ({max(lamp_hours, eq_hours)} hrs >= {threshold} hrs)"
            )

    def _parse_lamp_hours(self, resp):
        if not resp or len(resp) < 12:
            return None
        if list(bytearray(resp[:4])) != [0x05, 0x14, 0x00, 0x06]:
            return None
        data = bytearray(resp)
        return int.from_bytes(bytes(data[7:11]), byteorder="little", signed=False)

    def _log_rs232(self, device, direction, payload):
        should_log = self._log_tx if direction == "TX" else self._log_rx if direction == "RX" else False
        if not should_log:
            return
        message = f"{device.name} RS232 {direction}: {self._format_hex(payload)}"
        decoded = self._decode_payload(payload, is_rx=(direction == "RX")) if self._verbose_decode else None
        if decoded:
            message += f"  → {decoded}"
        indigo.server.log(message)

    def _get_ip_prop(self, device):
        return (device.pluginProps.get("address") or device.pluginProps.get("ipAddress") or "").strip()

    def _get_gc_address(self, device):
        ip = self._get_ip_prop(device)
        port = str(device.pluginProps.get("port", "")).strip()
        return f"{ip}:{port}" if ip and port else ip or ""

    def _get_input_delay_seconds(self, device):
        try:
            delay = int(device.pluginProps.get("inputDelaySeconds", 12))
        except Exception:
            delay = 12
        return max(0, delay)

    def _update_state_if_changed(self, device, key, value):
        try:
            if device.states.get(key) == value:
                return True
        except Exception:
            pass
        try:
            device.updateStateOnServer(key, value)
            return True
        except Exception:
            return False

    def _initialize_device_states(self, device_id):
        time.sleep(1.0)
        try:
            device = indigo.devices[device_id]
        except Exception:
            return
        self._update_state_if_changed(device, "power", "unknown")
        self._update_state_if_changed(device, "readyState", "false")
        self._update_state_if_changed(device, "rawState", 255)
        self._update_state_if_changed(device, "lastResponse", "")
        self._update_state_if_changed(device, "input", "unknown")
        self._update_state_if_changed(device, "rawInput", 255)
        self._update_state_if_changed(device, "lampHours", 0)
        self._update_state_if_changed(device, "equivalentLampHours", 0)
        self._update_state_if_changed(device, "maintenanceDue", "false")
        self._update_state_if_changed(device, "maintenanceSummary", "Lamp: 0 / {} hrs".format(self._format_int_commas(self._get_maintenance_alert_hours(device))) if self._get_maintenance_alert_hours(device) > 0 else "Lamp: 0 hrs")
        self._update_state_if_changed(device, "lastErrorSummary", "")
        self._transient_candidate[device_id] = None
        self._commanded_transition[device_id] = None
        self._pending_lamp_query_at[device_id] = 0
        self._last_lamp_query_at[device_id] = 0
        try:
            device.updateStateOnServer("gcAddress", self._get_gc_address(device))
        except Exception:
            pass
        try:
            device.updateStateOnServer("onOffState", False)
        except Exception:
            pass

    def deviceStartComm(self, device):
        try:
            device.stateListOrDisplayStateIdChanged()
        except Exception:
            pass
        init_t = threading.Thread(target=self._initialize_device_states, args=(device.id,), daemon=True)
        init_t.start()

        self._polling[device.id] = True
        self._locks.setdefault(device.id, threading.Lock())
        t = threading.Thread(target=self._pollLoop, args=(device.id,), daemon=True)
        t.start()

    def deviceStopComm(self, device):
        self._polling[device.id] = False
        self._transient_candidate.pop(device.id, None)
        self._commanded_transition.pop(device.id, None)
        self._pending_lamp_query_at.pop(device.id, None)
        self._last_lamp_query_at.pop(device.id, None)

    def closedDeviceConfigUi(self, valuesDict, userCancelled, typeId, devId):
        if userCancelled:
            return
        ip = (valuesDict.get("address") or valuesDict.get("ipAddress") or "").strip()
        if ip:
            valuesDict["address"] = ip
            valuesDict["ipAddress"] = ip

    def actionControlDevice(self, action, device):
        if action.deviceAction == indigo.kDeviceAction.TurnOn:
            self._sendPower(device, True)
        elif action.deviceAction == indigo.kDeviceAction.TurnOff:
            self._sendPower(device, False)

    def actionControlUniversal(self, action, device):
        try:
            if action.deviceAction == indigo.kUniversalAction.RequestStatus:
                indigo.server.log(f"Send Status Request -> {device.name}")
                self._refreshStatus(device, include_input=True, include_lamp=True)
            else:
                indigo.server.log(f"Unhandled universal action for {device.name}: {action.deviceAction}")
        except Exception as e:
            self.errorLog(f"exception in actionControlUniversal({device.name}): {e}")

    def powerOnAction(self, action):
        self._resolveAndSendPower(action, True)

    def powerOffAction(self, action):
        self._resolveAndSendPower(action, False)

    def refreshStatusAction(self, action):
        device = self._resolveDevice(action)
        if device:
            self._refreshStatus(device, include_input=True, include_lamp=True)

    def inputHdmi1Action(self, action):
        self._resolveAndSendInput(action, "hdmi1")

    def inputHdmi2Action(self, action):
        self._resolveAndSendInput(action, "hdmi2")

    def _resolveDevice(self, action):
        try:
            return indigo.devices[int(action.deviceId)]
        except Exception:
            self.errorLog(f"Could not resolve device from action.deviceId: {getattr(action,'deviceId',None)}")
            return None

    def _resolveAndSendPower(self, action, turn_on):
        device = self._resolveDevice(action)
        if not device:
            return
        self._sendPower(device, turn_on)

    def _resolveAndSendInput(self, action, input_name):
        device = self._resolveDevice(action)
        if not device:
            return
        self._sendInput(device, input_name)

    def _sendPower(self, device, turn_on):
        lock = self._locks.setdefault(device.id, threading.Lock())
        with lock:
            # Power commands on this projector are fire-and-forget; an immediate status query
            # can time out while the projector is transitioning. Defer verification to the poll loop.
            now = time.time()
            self._pause_until[device.id] = now + (5.0 if turn_on else 2.0)
            self._pending_input_query_at[device.id] = 0
            self._pending_lamp_query_at[device.id] = 0
            self._commanded_transition[device.id] = {
                "target": "on" if turn_on else "off",
                "until": now + (90.0 if turn_on else 120.0),
            }

            cmd = CMD_POWER_ON if turn_on else CMD_POWER_OFF
            ip = self._get_ip_prop(device)
            port = int(device.pluginProps.get("port", 4999))

            indigo.server.log(
                f"{'Power ON' if turn_on else 'Power OFF'} -> {device.name} ({ip}:{port}) bytes={self._format_hex(cmd)}"
            )
            self._log_rs232(device, "TX", cmd)

            ok = self._send_like_script(device, cmd)
            if not ok:
                return

            if turn_on:
                self._update_state_if_changed(device, "power", "warming")
                self._update_state_if_changed(device, "readyState", "false")
                self._set_onoff_if_present(device, False)
            else:
                # Reflect the requested transition immediately; the poll loop will confirm.
                self._update_state_if_changed(device, "power", "cooling")
                self._update_state_if_changed(device, "readyState", "false")
                self._set_onoff_if_present(device, False)

    def _sendInput(self, device, input_name):
        cmd = CMD_INPUT_HDMI1 if input_name == "hdmi1" else CMD_INPUT_HDMI2 if input_name == "hdmi2" else None
        if cmd is None:
            self.errorLog(f"{device.name}: unsupported input request {input_name}")
            return

        lock = self._locks.setdefault(device.id, threading.Lock())
        with lock:
            self._pause_until[device.id] = time.time() + 1.0
            self._pending_input_query_at[device.id] = time.time() + self._get_input_delay_seconds(device)
            ip = self._get_ip_prop(device)
            port = int(device.pluginProps.get("port", 4999))

            indigo.server.log(
                f"Input {INPUT_LABEL_MAP.get(input_name, input_name)} -> {device.name} ({ip}:{port}) bytes={self._format_hex(cmd)}"
            )
            self._log_rs232(device, "TX", cmd)

            ok = self._send_like_script(device, cmd)
            if not ok:
                return

            # Optimistically reflect the requested input; a later status query can refine it.
            self._update_state_if_changed(device, "input", input_name)
            self._update_state_if_changed(device, "rawInput", 0x03 if input_name == "hdmi1" else 0x07)

            time.sleep(0.2)
            self._refreshStatus_locked(device, include_input=False)

    def _send_like_script(self, device, payload):
        ip = self._get_ip_prop(device)
        port = int(device.pluginProps.get("port", 4999))
        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            s.connect((ip, port))
            s.send(bytearray(payload))
            time.sleep(0.1)
            return True
        except OSError as e:
            self.errorLog(f"{device.name}: connect/send failed to {ip}:{port}: {e}")
            return False
        finally:
            try:
                if s:
                    s.close()
            except Exception:
                pass

    def _sendAndRecv(self, device, payload):
        ip = self._get_ip_prop(device)
        port = int(device.pluginProps.get("port", 4999))
        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3.0)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            s.connect((ip, port))

            # Best-effort flush in case the bridge presents delayed bytes from a prior command.
            try:
                s.settimeout(0.05)
                while True:
                    stale = s.recv(1024)
                    if not stale:
                        break
                    self._log_rs232(device, "RX", stale)
            except Exception:
                pass
            finally:
                s.settimeout(3.0)

            self._log_rs232(device, "TX", payload)
            s.sendall(bytearray(payload))
            resp = s.recv(1024)
            self._log_rs232(device, "RX", resp)
            return resp
        except OSError as e:
            self.errorLog(f"{device.name}: send/recv failed to {ip}:{port}: {e}")
            return None
        finally:
            try:
                if s:
                    s.close()
            except Exception:
                pass

    def _refreshStatus(self, device, include_input=True, include_lamp=False):
        lock = self._locks.setdefault(device.id, threading.Lock())
        with lock:
            self._refreshStatus_locked(device, include_input=include_input, include_lamp=include_lamp)

    def _refreshStatus_locked(self, device, include_input=True, include_lamp=False):
        power_state = self._queryPower_locked(device)
        if power_state != "on":
            return
        if include_input:
            self._queryInput_locked(device)
            self._pending_input_query_at[device.id] = 0
        if include_lamp:
            self._queryLampHours_locked(device)
            self._pending_lamp_query_at[device.id] = 0

    def _queryPower(self, device):
        lock = self._locks.setdefault(device.id, threading.Lock())
        with lock:
            self._queryPower_locked(device)

    def _set_onoff_if_present(self, device, is_on: bool):
        try:
            device.updateStateOnServer("onOffState", bool(is_on))
        except Exception:
            pass

    def _queryPower_locked(self, device):
        resp = self._sendAndRecv(device, CMD_POWER_QUERY)
        if not resp:
            return "unknown"

        self._update_state_if_changed(device, "lastResponse", binascii.hexlify(resp).decode("ascii"))

        if len(resp) < 9:
            return "unknown"

        state = resp[7]
        self._update_state_if_changed(device, "rawState", int(state))
        try:
            device.updateStateOnServer("gcAddress", self._get_gc_address(device))
        except Exception:
            pass

        previous_power = device.states.get("power", "unknown")
        observed_power = POWER_STATE_MAP.get(state, "unknown")

        transition = self._commanded_transition.get(device.id)
        now = time.time()
        if transition and now >= transition.get("until", 0):
            transition = None
            self._commanded_transition[device.id] = None

        if transition:
            target = transition.get("target")
            if target == "off":
                if observed_power == "off":
                    self._commanded_transition[device.id] = None
                elif observed_power in ("on", "warming", "unknown"):
                    observed_power = "cooling" if previous_power in ("cooling", "off") else previous_power
            elif target == "on":
                if observed_power == "on":
                    self._commanded_transition[device.id] = None
                elif observed_power in ("off", "cooling", "unknown") and previous_power in ("warming", "on"):
                    observed_power = previous_power

        # Debounce transient warming/cooling states so a stray source-input reply does not
        # flip the device into COOLING/WARMING and fire triggers on every poll.
        power_state = observed_power
        if observed_power in ("warming", "cooling") and previous_power in ("on", "off"):
            candidate = self._transient_candidate.get(device.id)
            if candidate != observed_power:
                self._transient_candidate[device.id] = observed_power
                return previous_power
            self._transient_candidate[device.id] = None
        else:
            self._transient_candidate[device.id] = None

        self._update_state_if_changed(device, "power", power_state)
        self._update_state_if_changed(device, "readyState", "true" if power_state == "on" else "false")
        if power_state == "off":
            self._set_onoff_if_present(device, False)
            self._pending_input_query_at[device.id] = 0
            self._pending_lamp_query_at[device.id] = 0
            try:
                self._update_state_if_changed(device, "input", "unknown")
                self._update_state_if_changed(device, "rawInput", 0)
            except Exception:
                pass
        elif power_state == "on":
            self._set_onoff_if_present(device, True)
            if previous_power != "on" and self._pending_input_query_at.get(device.id, 0) <= 0:
                delay = self._get_input_delay_seconds(device)
                self._pending_input_query_at[device.id] = time.time() + delay
                self._pending_lamp_query_at[device.id] = time.time() + max(delay + 5, 20)
        else:
            self._set_onoff_if_present(device, False)
            self._pending_input_query_at[device.id] = 0
        return power_state

    def _queryInput_locked(self, device):
        resp = self._sendAndRecv(device, CMD_INPUT_QUERY)
        if not resp:
            return

        self._update_state_if_changed(device, "lastResponse", binascii.hexlify(resp).decode("ascii"))

        if len(resp) < 9:
            return

        raw_input = resp[7]
        if raw_input not in INPUT_STATE_MAP:
            return
        self._update_state_if_changed(device, "rawInput", int(raw_input))
        input_state = INPUT_STATE_MAP.get(raw_input, "unknown")
        self._update_state_if_changed(device, "input", input_state)


    def _queryLampHours_locked(self, device):
        usage_resp = self._sendAndRecv(device, CMD_LAMP_USAGE_QUERY)
        usage_hours = self._parse_lamp_hours(usage_resp)
        if usage_resp:
            self._update_state_if_changed(device, "lastResponse", binascii.hexlify(usage_resp).decode("ascii"))
        if usage_hours is not None:
            self._update_state_if_changed(device, "lampHours", int(usage_hours))

        eq_resp = self._sendAndRecv(device, CMD_LAMP_EQ_QUERY)
        eq_hours = self._parse_lamp_hours(eq_resp)
        if eq_resp:
            self._update_state_if_changed(device, "lastResponse", binascii.hexlify(eq_resp).decode("ascii"))
        if eq_hours is not None:
            self._update_state_if_changed(device, "equivalentLampHours", int(eq_hours))

        if usage_resp:
            err_summary = self._decode_payload(usage_resp, is_rx=True)
            if err_summary and err_summary.startswith("Error status:"):
                self._update_state_if_changed(device, "lastErrorSummary", err_summary)
        if eq_resp:
            err_summary = self._decode_payload(eq_resp, is_rx=True)
            if err_summary and err_summary.startswith("Error status:"):
                self._update_state_if_changed(device, "lastErrorSummary", err_summary)

        self._last_lamp_query_at[device.id] = time.time()
        self._update_maintenance_due(device)

    def _pollLoop(self, device_id):
        while self._polling.get(device_id, False):
            device = indigo.devices.get(device_id)
            if not device:
                break

            if time.time() < self._pause_until.get(device_id, 0):
                time.sleep(0.2)
                continue

            try:
                poll_s = int(device.pluginProps.get("pollSeconds", 30))
            except Exception:
                poll_s = 30

            self._refreshStatus(device, include_input=False, include_lamp=False)
            pending_at = self._pending_input_query_at.get(device_id, 0)
            if pending_at and time.time() >= pending_at:
                try:
                    lock = self._locks.setdefault(device.id, threading.Lock())
                    with lock:
                        if self._queryPower_locked(device) == "on":
                            self._queryInput_locked(device)
                finally:
                    self._pending_input_query_at[device_id] = 0

            lamp_pending_at = self._pending_lamp_query_at.get(device_id, 0)
            lamp_due = lamp_pending_at and time.time() >= lamp_pending_at
            periodic_due = self._last_lamp_query_at.get(device_id, 0) == 0 or (time.time() - self._last_lamp_query_at.get(device_id, 0) >= 21600)
            if lamp_due or periodic_due:
                try:
                    lock = self._locks.setdefault(device.id, threading.Lock())
                    with lock:
                        if self._queryPower_locked(device) == "on":
                            self._queryLampHours_locked(device)
                finally:
                    self._pending_lamp_query_at[device_id] = 0
            time.sleep(max(5, poll_s))
