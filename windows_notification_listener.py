import asyncio
import importlib.util
import logging
import sys
from typing import Any, Dict, List, Optional

from db import add_windows_notification

logger = logging.getLogger("botdc.windows_notifications")


class WindowsNotificationListener:
    def __init__(self, poll_interval: float = 5.0) -> None:
        self._poll_interval = poll_interval
        self._listener: Optional[Any] = None
        self._task: Optional[asyncio.Task[None]] = None
        self._stop_event = asyncio.Event()
        self._seen_notification_ids: set[int] = set()

    async def start(self) -> bool:
        if self._task:
            logger.info("WinRT listener je již spuštěn.")
            return True
        if sys.platform != "win32":
            logger.info("WinRT listener je dostupný pouze na Windows.")
            return False
        winsdk_spec = importlib.util.find_spec("winsdk")
        winrt_spec = importlib.util.find_spec("winrt")
        if winsdk_spec is None and winrt_spec is None:
            logger.warning("WinRT balíčky nejsou dostupné, listener nelze spustit.")
            return False

        if winsdk_spec is not None:
            from winsdk.windows.ui.notifications import NotificationKinds
            from winsdk.windows.ui.notifications.management import (
                UserNotificationListener,
                UserNotificationListenerAccessStatus,
            )
        else:
            from winrt.windows.ui.notifications import NotificationKinds
            from winrt.windows.ui.notifications.management import (
                UserNotificationListener,
                UserNotificationListenerAccessStatus,
            )

        listener = self._get_listener(UserNotificationListener)
        if listener is None:
            logger.error(
                "WinRT listener nelze inicializovat (chybí get_current/current)."
            )
            return False
        access = await listener.request_access_async()
        if access != UserNotificationListenerAccessStatus.ALLOWED:
            logger.warning(
                "WinRT listener nemá přístup k notifikacím (%s).", access
            )
            return False

        self._listener = listener
        self._notification_kinds = self._resolve_notification_kind(NotificationKinds)
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="winrt-notification-loop")
        logger.info("WinRT listener spuštěn.")
        return True

    async def stop(self) -> None:
        if not self._task:
            return
        self._stop_event.set()
        await self._task
        self._task = None
        logger.info("WinRT listener zastaven.")

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            await self._poll_notifications()
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._poll_interval
                )
            except asyncio.TimeoutError:
                continue

    async def _poll_notifications(self) -> None:
        if not self._listener:
            return
        try:
            get_notifications = getattr(self._listener, "get_notifications_async", None)
            if not callable(get_notifications):
                raise AttributeError("get_notifications_async není k dispozici")
            notifications = await get_notifications(self._notification_kinds)
        except Exception:
            logger.exception("Načtení WinRT notifikací selhalo.")
            return

        for notification in notifications:
            try:
                notification_id = int(notification.id)
            except Exception:
                logger.exception("Notifikace nemá validní ID, přeskakuji.")
                continue
            if notification_id in self._seen_notification_ids:
                continue

            payload = self._build_payload(notification)
            add_windows_notification(payload)
            self._seen_notification_ids.add(notification_id)

    def _build_payload(self, notification: Any) -> Dict[str, Any]:
        app_name = self._extract_app_name(notification)
        texts = self._extract_texts(notification)
        payload: Dict[str, Any] = {
            "app_name": app_name,
            "text": "\n".join(texts).strip(),
            "raw": {
                "id": int(getattr(notification, "id", 0)),
                "app_id": getattr(getattr(notification, "app_info", None), "app_user_model_id", None),
                "app_name": app_name,
                "texts": texts,
                "creation_time": self._format_creation_time(notification),
            },
        }
        return payload

    def _get_listener(self, listener_cls: Any) -> Optional[Any]:
        get_current = getattr(listener_cls, "get_current", None)
        if callable(get_current):
            return get_current()
        current = getattr(listener_cls, "current", None)
        if callable(current):
            return current()
        if current is not None:
            return current
        return None

    def _extract_app_name(self, notification: Any) -> str:
        try:
            app_info = notification.app_info
            display_info = app_info.display_info
            return display_info.display_name or ""
        except Exception:
            return ""

    def _extract_texts(self, notification: Any) -> List[str]:
        texts: List[str] = []
        try:
            visual = notification.notification.visual
            binding = visual.get_binding(0)
            if binding is None:
                return texts
            for element in binding.get_text_elements():
                if element and element.text:
                    texts.append(str(element.text))
        except Exception:
            logger.exception("Nepodařilo se extrahovat text notifikace.")
        return texts

    def _format_creation_time(self, notification: Any) -> Optional[str]:
        try:
            creation_time = notification.creation_time
            return creation_time.isoformat()
        except Exception:
            return None

    def _is_access_allowed(self, access: Any, status_enum: Any) -> bool:
        allowed_value = getattr(status_enum, "ALLOWED", None)
        if allowed_value is not None and access == allowed_value:
            return True
        name = getattr(access, "name", None)
        if isinstance(name, str) and name.lower() == "allowed":
            return True
        return str(access).lower().endswith("allowed")

    def _resolve_notification_kind(self, kinds_enum: Any) -> Any:
        if hasattr(kinds_enum, "TOAST"):
            return kinds_enum.TOAST
        return getattr(kinds_enum, "toast", kinds_enum)
