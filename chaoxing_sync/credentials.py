import base64
import ctypes
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path

from .aes import decrypt_cbc, encrypt_cbc


PLUGIN_ID = "com.msyark.chaoxingsync"
DPAPI_PREFIX = "dpapi1:"
AES_PREFIX = "aes1:"


class CredentialError(Exception):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_uint32),
        ("data", ctypes.POINTER(ctypes.c_byte)),
    ]


def _dpapi(data: bytes, protect: bool) -> bytes:
    if os.name != "nt":
        raise OSError("DPAPI is only available on Windows")
    buffer = ctypes.create_string_buffer(data, len(data))
    source = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    target = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    function.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.c_wchar_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(_DataBlob),
    ]
    function.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise OSError(ctypes.GetLastError())
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel32.LocalFree(target.data)


def _data_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "ClassWidgets2" / "plugin-data" / PLUGIN_ID


def _fallback_key() -> bytes:
    directory = _data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    path = directory / "secret.key"
    if path.exists():
        key = path.read_bytes()
        if len(key) == 32:
            return key
        raise CredentialError("本地密钥文件损坏，请重新设置账号")
    key = secrets.token_bytes(32)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, key)
    finally:
        os.close(descriptor)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


def _protect_payload(payload: bytes) -> str:
    if os.name == "nt":
        try:
            return DPAPI_PREFIX + base64.b64encode(_dpapi(payload, True)).decode("ascii")
        except Exception:
            pass
    key = _fallback_key()
    iv = secrets.token_bytes(16)
    ciphertext = encrypt_cbc(key, iv, payload)
    mac = hmac.new(key, iv + ciphertext, hashlib.sha256).digest()
    return AES_PREFIX + base64.b64encode(iv + ciphertext + mac).decode("ascii")


def _unprotect_payload(blob: str) -> bytes:
    if not blob:
        raise CredentialError("尚未设置加密数据")
    try:
        if blob.startswith(DPAPI_PREFIX):
            return _dpapi(base64.b64decode(blob[len(DPAPI_PREFIX):]), False)
        if blob.startswith(AES_PREFIX):
            raw = base64.b64decode(blob[len(AES_PREFIX):])
            if len(raw) < 48:
                raise ValueError("invalid payload")
            iv, ciphertext, mac = raw[:16], raw[16:-32], raw[-32:]
            key = _fallback_key()
            expected = hmac.new(key, iv + ciphertext, hashlib.sha256).digest()
            if not hmac.compare_digest(mac, expected):
                raise ValueError("integrity check failed")
            return decrypt_cbc(key, iv, ciphertext)
        raise ValueError("unknown format")
    except CredentialError:
        raise
    except Exception as error:
        raise CredentialError("无法解密已保存的数据") from error


def protect_account(username: str, password: str) -> str:
    payload = json.dumps({"u": username, "p": password}, ensure_ascii=False).encode("utf-8")
    return _protect_payload(payload)


def unprotect_account(blob: str) -> tuple[str, str]:
    try:
        data = json.loads(_unprotect_payload(blob).decode("utf-8"))
        username = str(data.get("u") or "")
        password = str(data.get("p") or "")
        if not username or not password:
            raise ValueError("empty credentials")
        return username, password
    except CredentialError:
        raise
    except Exception as error:
        raise CredentialError("无法解密已保存的账号，请重新设置账号") from error


def protect_text(text: str) -> str:
    payload = json.dumps({"t": str(text or "")}, ensure_ascii=False).encode("utf-8")
    return _protect_payload(payload)


def unprotect_text(blob: str) -> str:
    try:
        data = json.loads(_unprotect_payload(blob).decode("utf-8"))
        return str(data.get("t") or "")
    except CredentialError:
        raise
    except Exception as error:
        raise CredentialError("无法解密已保存的数据") from error
