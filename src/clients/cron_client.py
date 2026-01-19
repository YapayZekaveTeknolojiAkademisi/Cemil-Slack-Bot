import logging
import asyncio
from datetime import datetime, timedelta
from typing import Callable, List, Dict, Any, Optional
from apscheduler.schedulers.background import BackgroundScheduler
from src.core.logger import logger
from src.core.exceptions import CemilBotError
from src.core.singleton import SingletonMeta

class CronClient(metaclass=SingletonMeta):
    """
    Cemil Bot için merkezi zamanlanmış görev (Cron) yönetim sınıfı.
    BackgroundScheduler kullanarak işleri ayrı thread'lerde yönetir.
    Async fonksiyonları otomatik olarak sarmalar.
    """

    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self._is_running = False

    def start(self):
        """Zamanlayıcıyı başlatır."""
        if not self._is_running:
            self.scheduler.start()
            self._is_running = True
            logger.info("[i] CronClient (BackgroundScheduler) başlatıldı.")

    def shutdown(self, wait: bool = True):
        """Zamanlayıcıyı kapatır."""
        if self._is_running:
            self.scheduler.shutdown(wait=wait)
            self._is_running = False
            logger.info("[i] CronClient (Zamanlayıcı) kapatıldı.")

    def _wrap_async(self, func: Callable, args: List):
        """Async fonksiyonları senkron wrapper içine alır."""
        if asyncio.iscoroutinefunction(func):
            def wrapper(*a, **k):
                try:
                    asyncio.run(func(*a, **k))
                except Exception as e:
                    logger.error(f"[X] Async cron görevi hatası: {e}")
            return wrapper, args
        return func, args

    def add_cron_job(self, func: Callable, cron_expression: Dict[str, Any], job_id: Optional[str] = None, args: Optional[List] = None) -> str:
        """
        Düzenli bir cron görevi ekler.
        """
        try:
            wrapped_func, wrapped_args = self._wrap_async(func, args or [])
            
            job = self.scheduler.add_job(
                wrapped_func,
                'cron',
                id=job_id,
                args=wrapped_args,
                **cron_expression
            )
            logger.info(f"[+] Cron görevi eklendi: {job.id} ({cron_expression})")
            return job.id
        except Exception as e:
            logger.error(f"[X] Cron görevi eklenirken hata: {e}")
            raise CemilBotError(f"Cron işi eklenemedi: {e}")

    def add_once_job(self, func: Callable, run_date: Optional[datetime] = None, delay_minutes: Optional[int] = None, job_id: Optional[str] = None, args: Optional[List] = None) -> str:
        """
        Bir kez çalışacak bir görev ekler.
        """
        if delay_minutes is not None:
            run_date = datetime.now() + timedelta(minutes=delay_minutes)
        
        if not run_date:
            raise CemilBotError("Birim zamanlı iş için run_date veya delay_minutes gereklidir.")

        try:
            wrapped_func, wrapped_args = self._wrap_async(func, args or [])

            job = self.scheduler.add_job(
                wrapped_func,
                'date',
                id=job_id,
                run_date=run_date,
                args=wrapped_args,
                replace_existing=True  # Aynı job_id varsa güncelle
            )
            logger.info(f"[+] Tek seferlik görev planlandı: {job.id} (Çalışma: {run_date})")
            return job.id
        except Exception as e:
            # ConflictingIdError durumunda (replace_existing=True ile bu olmamalı ama yine de kontrol)
            error_str = str(e)
            if "conflicts with an existing job" in error_str.lower() or "ConflictingIdError" in error_str:
                logger.warning(f"[!] Job zaten planlanmış, atlanıyor: {job_id}")
                return job_id  # Job zaten var, ID'yi döndür
            logger.error(f"[X] Tek seferlik görev planlanırken hata: {e}")
            raise CemilBotError(f"Tek seferlik iş planlanamadı: {e}")

    def remove_job(self, job_id: str):
        """Planlanmış bir görevi kaldırır."""
        try:
            self.scheduler.remove_job(job_id)
            logger.info(f"[-] Görev kaldırıldı: {job_id}")
            return True
        except Exception as e:
            logger.warning(f"[!] Görev kaldırılırken hata (belki zaten bitti/silindi): {job_id}")
            return False

    def list_jobs(self) -> List[Dict[str, Any]]:
        """Tüm aktif görevleri listeler."""
        jobs = self.scheduler.get_jobs()
        job_list = []
        for job in jobs:
            job_list.append({
                "id": job.id,
                "next_run_time": str(job.next_run_time),
                # Wrapper yüzünden orijinal fonksiyon ismini alamayabiliriz
                "func": str(job.func)
            })
        logger.info(f"[i] Toplam {len(job_list)} aktif görev listelendi.")
        return job_list
