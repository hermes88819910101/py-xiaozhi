"""Windows 音量后端（pycaw / comtypes）.

无默认音频输出设备时（GetDefaultAudioEndpoint 抛 ERROR_NOT_FOUND，
HRESULT 0x80070490），初始化不再抛异常：标记为不可用，后续访问时
惰性重试，直到设备出现。避免在启动时污染 error.log。
"""

from __future__ import annotations

from typing import Any

from src.logging import get_logger

logger = get_logger()

# ERROR_NOT_FOUND: GetDefaultAudioEndpoint 在无默认渲染设备时抛出
_NOT_FOUND_HRESULTS = (-2147023728, 0x80070490)


def _is_no_device_error(e: Exception) -> bool:
    args = getattr(e, "args", ())
    for a in args:
        try:
            if int(a) in _NOT_FOUND_HRESULTS:
                return True
        except (TypeError, ValueError):
            continue
    return False


class WindowsVolumeBackend:
    def __init__(self) -> None:
        self._module_cache: dict[str, Any] = {}
        self.volume_control = None
        self._warned = False
        self._init()  # 失败不抛异常：记录警告并标记不可用，等待惰性重试

    @property
    def available(self) -> bool:
        return self.volume_control is not None

    def _lazy_import(self, module_name: str, attr: str | None = None) -> Any:
        if module_name in self._module_cache:
            module = self._module_cache[module_name]
        else:
            module = __import__(
                module_name, fromlist=["*"] if "." in module_name else []
            )
            self._module_cache[module_name] = module
        if attr:
            return getattr(module, attr)
        return module

    def _init(self) -> bool:
        """初始化 COM 音量控制；成功返回 True，失败返回 False（不抛异常）。"""
        if self.volume_control is not None:
            return True
        try:
            POINTER = self._lazy_import("ctypes", "POINTER")
            cast = self._lazy_import("ctypes", "cast")
            CLSCTX_ALL = self._lazy_import("comtypes", "CLSCTX_ALL")
            AudioUtilities = self._lazy_import("pycaw.pycaw", "AudioUtilities")
            IAudioEndpointVolume = self._lazy_import(
                "pycaw.pycaw", "IAudioEndpointVolume"
            )

            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(
                IAudioEndpointVolume._iid_, CLSCTX_ALL, None
            )
            self.volume_control = cast(interface, POINTER(IAudioEndpointVolume))
            logger.debug("Windows音量控制初始化成功")
            return True
        except Exception as e:
            if not self._warned:
                self._warned = True
                if _is_no_device_error(e):
                    logger.warning(
                        "Windows音量控制不可用：未检测到默认音频输出设备"
                        "（将在设备出现后自动重试）"
                    )
                else:
                    logger.warning(f"Windows音量控制初始化失败: {e}")
                    logger.debug("Windows音量控制初始化失败详情", exc_info=True)
            return False

    def _ensure(self) -> bool:
        """惰性重试初始化（设备可能在运行中途插拔/出现）。"""
        return self.volume_control is not None or self._init()

    def get_volume(self) -> int:
        try:
            if not self._ensure():
                return 70
            volume_scalar = self.volume_control.GetMasterVolumeLevelScalar()
            return int(volume_scalar * 100)
        except Exception as e:
            logger.warning(f"获取Windows音量失败: {e}")
            return 70

    def set_volume(self, volume: int) -> None:
        try:
            if not self._ensure():
                logger.warning("音量控制不可用（无默认音频输出设备），无法设置音量")
                return
            self.volume_control.SetMasterVolumeLevelScalar(volume / 100.0, None)
        except Exception as e:
            logger.warning(f"设置Windows音量失败: {e}")
