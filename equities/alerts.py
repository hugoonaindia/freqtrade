"""Webhooks y notificaciones para eventos críticos."""

import json
import logging
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from queue import Queue
from typing import Optional

logger = logging.getLogger("freqtrade_equities")


class AlertManager:
    """Gestor de alerts y webhooks para eventos de trading."""
    
    def __init__(self, state_dir: Path, config_path: Optional[Path] = None):
        self.state_dir = state_dir
        self.config_path = config_path or state_dir / "alert_config.json"
        self.queue = Queue(maxsize=100)
        self.worker = None
        self.running = False
        self.lock = threading.Lock()
        
        # Cargar configuración
        self.config = self._load_config()
        
        # Tracking de alerts para evitar spam
        self.last_alerts = {}  # {alert_type: timestamp}
        self.alert_cooldowns = {
            "stop_triggered": 300,      # 5 min
            "fill_partial": 60,         # 1 min
            "error_monitor": 60,        # 1 min
            "order_timeout": 60,        # 1 min
            "account_stop": 300,        # 5 min
            "connection_lost": 300,     # 5 min
        }
    
    def _load_config(self) -> dict:
        """Cargar configuración de webhooks."""
        if self.config_path.exists():
            try:
                config = json.loads(self.config_path.read_text())
                logger.info("Configuración de webhooks cargada: %s", list(config.keys()))
                return config
            except Exception as e:
                logger.warning("Error cargando config de webhooks: %s", e)
        return {
            "telegram": None,
            "pushover": None,
            "teams": None,
            "email": None,
            "enabled": True,
        }
    
    def save_config(self, config: dict):
        """Guardar configuración de webhooks."""
        with self.lock:
            self.config = {**self.config, **config}
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text(json.dumps(self.config, indent=2))
            logger.info("Configuración de webhooks guardada")
    
    def start(self):
        """Iniciar worker de alerts."""
        if self.running:
            return
        self.running = True
        self.worker = threading.Thread(target=self._process_queue, daemon=True)
        self.worker.start()
        logger.info("AlertManager iniciado")
    
    def stop(self):
        """Detener worker de alerts."""
        self.running = False
        if self.worker:
            self.worker.join(timeout=5)
        logger.info("AlertManager detenido")
    
    def _process_queue(self):
        """Procesar cola de alerts en background."""
        while self.running:
            try:
                alert = self.queue.get(timeout=1)
                self._send_alert(alert)
                self.queue.task_done()
            except Exception:
                continue
    
    def _should_send(self, alert_type: str) -> bool:
        """Verificar si se debe enviar alerta (cooldown)."""
        now = time.time()
        last = self.last_alerts.get(alert_type, 0)
        cooldown = self.alert_cooldowns.get(alert_type, 60)
        
        if now - last < cooldown:
            return False
        
        with self.lock:
            self.last_alerts[alert_type] = now
        return True
    
    def _send_alert(self, alert: dict):
        """Enviar alerta a todos los endpoints habilitados."""
        if not self.config.get("enabled", True):
            return
        
        alert_type = alert.get("type", "unknown")
        if not self._should_send(alert_type):
            logger.debug("Alerta %s suprimida por cooldown", alert_type)
            return
        
        # Formatear mensaje
        message = self._format_message(alert)
        severity = alert.get("severity", "warning")
        
        # Enviar a cada endpoint
        if self.config.get("telegram") and self.config["telegram"].get("enabled"):
            self._send_telegram(message, severity)
        if self.config.get("pushover") and self.config["pushover"].get("enabled"):
            self._send_pushover(message, severity)
        if self.config.get("teams") and self.config["teams"].get("enabled"):
            self._send_teams(message, severity)
        if self.config.get("email") and self.config["email"].get("enabled"):
            self._send_email(message, severity)
    
    def _format_message(self, alert: dict) -> str:
        """Formatear mensaje de alerta."""
        alert_type = alert.get("type", "unknown")
        symbol = alert.get("symbol", "N/A")
        message = alert.get("message", "Evento sin descripción")
        
        if alert_type == "stop_triggered":
            return f"🛑 STOP TRIGGERED: {symbol}\n{message}"
        elif alert_type == "fill_partial":
            return f"⚠️ FILL PARCIAL: {symbol}\n{message}"
        elif alert_type == "error_monitor":
            return f"❌ ERROR MONITOR: {message}"
        elif alert_type == "order_timeout":
            return f"⏳ ORDER TIMEOUT: {symbol}\n{message}"
        elif alert_type == "account_stop":
            return f"🛑 LÍMITE GLOBAL: {message}"
        elif alert_type == "connection_lost":
            return f"🔌 CONEXIÓN PERDIDA: {message}"
        else:
            return f"📊 {alert_type.upper()}: {symbol}\n{message}"
    
    def _send_telegram(self, message: str, severity: str):
        """Enviar alerta a Telegram."""
        try:
            import requests
            
            config = self.config["telegram"]
            token = config.get("bot_token")
            chat_id = config.get("chat_id")
            
            if not token or not chat_id:
                logger.warning("Telegram: token o chat_id faltantes")
                return
            
            # Formatear para Markdown
            text = f"*{severity.upper()}*\n{message}"
            
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "Markdown",
                "disable_notification": severity != "critical",
            }
            
            response = requests.post(url, json=payload, timeout=5)
            if response.status_code == 200:
                logger.info("Telegram: alerta enviada")
            else:
                logger.warning("Telegram: error %d", response.status_code)
        except ImportError:
            logger.warning("Telegram: paquete requests no instalado")
        except Exception as e:
            logger.error("Telegram: error %s", e)
    
    def _send_pushover(self, message: str, severity: str):
        """Enviar alerta a Pushover."""
        try:
            import requests
            
            config = self.config["pushover"]
            token = config.get("app_token")
            user_key = config.get("user_key")
            
            if not token or not user_key:
                logger.warning("Pushover: app_token o user_key faltantes")
                return
            
            priority = 1 if severity in ("critical", "warning") else 0
            
            url = "https://api.pushover.net/1/messages.json"
            payload = {
                "token": token,
                "user": user_key,
                "message": message,
                "priority": priority,
                "title": f"Alpaca Paper [{severity}]",
            }
            
            response = requests.post(url, data=payload, timeout=5)
            if response.status_code == 200:
                logger.info("Pushover: alerta enviada")
            else:
                logger.warning("Pushover: error %d", response.status_code)
        except ImportError:
            logger.warning("Pushover: paquete requests no instalado")
        except Exception as e:
            logger.error("Pushover: error %s", e)
    
    def _send_teams(self, message: str, severity: str):
        """Enviar alerta a Microsoft Teams."""
        try:
            import requests
            
            config = self.config["teams"]
            webhook_url = config.get("webhook_url")
            
            if not webhook_url:
                logger.warning("Teams: webhook_url faltante")
                return
            
            color = "d63333" if severity in ("critical", "warning") else "663399"
            
            payload = {
                "@type": "MessageCard",
                "@context": "http://schema.org/extensions",
                "summary": f"Alpaca Paper {severity}",
                "themeColor": color,
                "title": f"Alpaca Paper [{severity.upper()}]",
                "text": message,
            }
            
            response = requests.post(webhook_url, json=payload, timeout=5)
            if response.status_code == 200:
                logger.info("Teams: alerta enviada")
            else:
                logger.warning("Teams: error %d", response.status_code)
        except ImportError:
            logger.warning("Teams: paquete requests no instalado")
        except Exception as e:
            logger.error("Teams: error %s", e)
    
    def _send_email(self, message: str, severity: str):
        """Enviar alerta por email."""
        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart
            
            config = self.config["email"]
            smtp_host = config.get("smtp_host")
            smtp_port = config.get("smtp_port", 587)
            smtp_user = config.get("smtp_user")
            smtp_pass = config.get("smtp_pass")
            from_addr = config.get("from_addr")
            to_addr = config.get("to_addr")
            
            if not all([smtp_host, smtp_user, smtp_pass, from_addr, to_addr]):
                logger.warning("Email: configuración incompleta")
                return
            
            msg = MIMEMultipart()
            msg["From"] = from_addr
            msg["To"] = to_addr
            msg["Subject"] = f"[{severity.upper()}] Alpaca Paper: {message[:50]}"
            msg.attach(MIMEText(message, "plain"))
            
            server = smtplib.SMTP(smtp_host, smtp_port)
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(from_addr, to_addr, msg.as_string())
            server.quit()
            
            logger.info("Email: alerta enviada")
        except ImportError:
            logger.warning("Email: paquetes smtplib no disponibles")
        except Exception as e:
            logger.error("Email: error %s", e)
    
    # Convenience methods for common alerts
    
    def stop_triggered(self, symbol: str, price: float, stop_price: float, qty: int):
        """Alerta cuando se dispara un stop."""
        self.queue.put({
            "type": "stop_triggered",
            "symbol": symbol,
            "message": f"Price {price:.2f} <= Stop {stop_price:.2f} | Qty: {qty}",
            "severity": "critical",
        })
    
    def fill_partial(self, symbol: str, filled_qty: int, total_qty: int, order_id: str):
        """Alerta cuando una orden se llena parcialmente."""
        self.queue.put({
            "type": "fill_partial",
            "symbol": symbol,
            "message": f"Filled {filled_qty}/{total_qty} | Order: {order_id[:8]}...",
            "severity": "warning",
        })
    
    def order_timeout(self, symbol: str, order_id: str, age_seconds: int):
        """Alerta cuando una orden está pendiente demasiado tiempo."""
        self.queue.put({
            "type": "order_timeout",
            "symbol": symbol,
            "message": f"Order age: {age_seconds}s (limit: 120s) | ID: {order_id[:8]}...",
            "severity": "warning",
        })
    
    def account_stop_triggered(self, current_equity: float, baseline: float, loss_pct: float):
        """Alerta cuando se dispara el límite global de cuenta."""
        self.queue.put({
            "type": "account_stop",
            "symbol": "GLOBAL",
            "message": f"Equity {current_equity:.2f} < Floor {baseline * 0.98:.2f} | Loss: {loss_pct:.2f}%",
            "severity": "critical",
        })
    
    def monitor_error(self, error: str):
        """Alerta cuando el supervisor encuentra error."""
        self.queue.put({
            "type": "error_monitor",
            "symbol": "SYSTEM",
            "message": error[:200],
            "severity": "warning",
        })
    
    def connection_lost(self, service: str):
        """Alerta cuando se pierde conexión con servicio."""
        self.queue.put({
            "type": "connection_lost",
            "symbol": service,
            "message": "Conexión perdida con el servicio",
            "severity": "warning",
        })


# Función auxiliar para crear un manager global
_alert_manager: Optional[AlertManager] = None


def get_alert_manager(state_dir: Path, config_path: Optional[Path] = None) -> AlertManager:
    """Obtener o crear el manager global de alerts."""
    global _alert_manager
    if _alert_manager is None:
        _alert_manager = AlertManager(state_dir, config_path)
    return _alert_manager


def stop_alert_manager():
    """Detener el manager global de alerts."""
    global _alert_manager
    if _alert_manager:
        _alert_manager.stop()
        _alert_manager = None
