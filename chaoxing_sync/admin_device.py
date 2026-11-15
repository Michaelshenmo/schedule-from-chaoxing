import base64
import hashlib
import json
import time
import uuid
from pathlib import Path

import ecdsa
import requests
from ecdsa.util import sigencode_der

from .credentials import _data_dir, protect_text, unprotect_text
from .timeline_server import parse_timeline_payload


HANGZHI_BASE = "https://api.edu505.com/sa"
STATE_FILE = "admin_device.json"


class AdminDeviceError(Exception):
    pass


def _normalize_server(value: str) -> str:
    server = str(value or "").strip()
    if not server:
        raise AdminDeviceError("未设置时间线服务器地址")
    if not server.lower().startswith(("http://", "https://")):
        server = "http://" + server
    return server.rstrip("/")


def _state_path() -> Path:
    directory = _data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    try:
        directory.chmod(0o700)
    except OSError:
        pass
    return directory / STATE_FILE


def _load_state() -> dict:
    path = _state_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(state: dict):
    path = _state_path()
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        temp.chmod(0o600)
    except OSError:
        pass
    temp.replace(path)


def _post(server: str, path: str, payload: dict) -> dict:
    try:
        response = requests.post(
            server + path,
            json=payload,
            timeout=(8, 20),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
    except requests.RequestException as error:
        raise AdminDeviceError(f"请求 admin-system 失败：{error}") from error
    try:
        data = response.json()
    except ValueError as error:
        raise AdminDeviceError(f"admin-system 返回异常：HTTP {response.status_code}") from error
    if response.status_code >= 400 or not isinstance(data, dict) or data.get("code") != 0:
        message = data.get("msg") if isinstance(data, dict) else ""
        raise AdminDeviceError(message or f"admin-system 返回 HTTP {response.status_code}")
    return data.get("data") or {}


def _vendor_login(sn: str) -> str:
    try:
        response = requests.post(
            HANGZHI_BASE + "/register/queryDeviceToken",
            json={"snNumber": sn, "imeiNumber": "", "mac": ""},
            timeout=(8, 20),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
    except requests.RequestException as error:
        raise AdminDeviceError(f"请求设备认证失败：{error}") from error
    try:
        data = response.json()
    except ValueError as error:
        raise AdminDeviceError("设备认证返回格式异常") from error
    if not isinstance(data, dict) or data.get("code") != 0:
        message = data.get("msg") if isinstance(data, dict) else ""
        raise AdminDeviceError(message or "设备 SN 认证失败")
    token = (data.get("data") or {}).get("access-token")
    if not token:
        raise AdminDeviceError("设备认证未返回访问令牌")
    return str(token)


def _vendor_user(token: str) -> dict:
    try:
        response = requests.get(
            HANGZHI_BASE + "/device/queryDeviceDetailInfo.svl",
            headers={"access-token": token, "Accept": "application/json"},
            timeout=(8, 20),
        )
    except requests.RequestException as error:
        raise AdminDeviceError(f"请求设备信息失败：{error}") from error
    try:
        data = response.json()
    except ValueError as error:
        raise AdminDeviceError("设备信息返回格式异常") from error
    if not isinstance(data, dict) or data.get("code") != 0 or not isinstance(data.get("data"), dict):
        raise AdminDeviceError("设备信息获取失败")
    return data["data"]


def _sign(key, content: str) -> str:
    signature = key.sign_deterministic(
        content.encode("utf-8"),
        hashfunc=hashlib.sha256,
        sigencode=sigencode_der,
    )
    return base64.b64encode(signature).decode("ascii")


def _ensure_key(state: dict):
    encoded = str(state.get("privateKey") or "")
    if encoded:
        try:
            raw = base64.b64decode(unprotect_text(encoded))
            return ecdsa.SigningKey.from_string(raw, curve=ecdsa.NIST256p)
        except Exception:
            pass
    key = ecdsa.SigningKey.generate(curve=ecdsa.NIST256p)
    state["privateKey"] = protect_text(base64.b64encode(key.to_string()).decode("ascii"))
    state.setdefault("installationId", str(uuid.uuid4()))
    _save_state(state)
    return key


def _enroll(server: str, state: dict, vendor_token: str, device_id: str) -> dict:
    key = _ensure_key(state)
    installation = str(state.get("installationId") or uuid.uuid4())
    state["installationId"] = installation
    public = base64.b64encode(key.get_verifying_key().to_der()).decode("ascii")
    data = _post(server, "/api/device/enrollment", {
        "installationId": installation,
        "deviceId": int(device_id),
        "publicKey": public,
        "signature": _sign(key, f"ENROLL\n{installation}\n{device_id}"),
        "vendorToken": vendor_token,
    })
    request_id = str(data.get("requestId") or "")
    nonce = str(data.get("nonce") or "")
    if not request_id or not nonce:
        raise AdminDeviceError("设备注册未返回请求编号")
    for attempt in range(6):
        if attempt:
            time.sleep(1)
        reply = _post(server, "/api/device/enrollment/status", {
            "requestId": request_id,
            "signature": _sign(key, f"ENROLL_POLL\n{request_id}\n{nonce}"),
            "vendorToken": vendor_token,
        })
        status = str(reply.get("status") or "")
        if status == "APPROVED":
            binding = reply.get("binding") or {}
            if not binding.get("bindingId"):
                raise AdminDeviceError("设备注册已通过但未返回绑定信息")
            binding["server"] = server
            binding["sn"] = state.get("sn")
            state["binding"] = binding
            _save_state(state)
            return binding
        if status not in ("PENDING",):
            raise AdminDeviceError(f"设备注册失败：{status or '未知状态'}")
    raise AdminDeviceError("设备注册仍在审核中，请联系管理员")


def _policy(server: str, state: dict, binding: dict) -> dict:
    key = _ensure_key(state)
    binding_id = str(binding.get("bindingId") or "")
    if not binding_id:
        raise AdminDeviceError("设备绑定信息缺失")
    challenge = _post(server, "/api/device/challenge", {"bindingId": binding_id})
    nonce = str(challenge.get("nonce") or "")
    if not nonce:
        raise AdminDeviceError("设备认证未返回 nonce")
    raw = "{}"
    signature = _sign(key, f"{binding_id}\n{nonce}\n/api/device/policy\n{raw}")
    reply = _post(server, "/api/device/policy", {
        "bindingId": binding_id,
        "nonce": nonce,
        "requestPayload": raw,
        "signature": signature,
    })
    envelopes = reply.get("envelopes") or []
    documents = []
    for envelope in envelopes:
        if not isinstance(envelope, dict):
            continue
        payload = envelope.get("payload")
        if not payload:
            continue
        try:
            document = json.loads(base64.b64decode(payload).decode("utf-8"))
        except Exception:
            continue
        if isinstance(document, dict):
            documents.append(document)
    if not documents:
        raise AdminDeviceError("时间线策略为空")
    now = int(time.time())
    valid = [
        document for document in documents
        if int(document.get("validFrom") or 0) <= now < int(document.get("offlineUsableUntil") or 0)
    ]
    document = max(valid or documents, key=lambda item: int(item.get("revision") or 0))
    schedule = document.get("schedule") or {}
    grouped = parse_timeline_payload({"data": {"schedule": schedule}})
    return grouped


def fetch_admin_timeline(server: str, sn: str) -> dict:
    server = _normalize_server(server)
    sn = str(sn or "").strip()
    if not sn:
        raise AdminDeviceError("请填写设备 SN")
    state = _load_state()
    if state.get("sn") != sn:
        state = {"sn": sn, "server": server}
        _save_state(state)
    elif state.get("server") != server:
        state["server"] = server
        state["binding"] = {}
        _save_state(state)
    vendor_token = _vendor_login(sn)
    user = _vendor_user(vendor_token)
    device_id = str(user.get("deviceId") or "").strip()
    if not device_id:
        raise AdminDeviceError("设备信息中缺少 deviceId")
    binding = state.get("binding") or {}
    if str(binding.get("deviceId") or "") != device_id or not binding.get("bindingId"):
        binding = _enroll(server, state, vendor_token, device_id)
    try:
        return _policy(server, state, binding)
    except AdminDeviceError as error:
        message = str(error)
        if "绑定" not in message and "认证" not in message and "401" not in message:
            raise
        state["binding"] = {}
        _save_state(state)
        binding = _enroll(server, state, vendor_token, device_id)
        return _policy(server, state, binding)
