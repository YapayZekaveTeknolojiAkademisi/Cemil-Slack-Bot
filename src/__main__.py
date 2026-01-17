import sys
import os
import time
import signal
import atexit

# Kullanıcıya anında geri bildirim ver
print("\n[INIT] Cemil Bot başlatılıyor...")
print("[INIT] Gerekli yapay zeka kütüphaneleri (Torch, SciPy, Transformers) yükleniyor. Bu işlem ilk seferde biraz zaman alabilir, lütfen bekleyin...\n")

# Proje kök dizinini sys.path'e ekle
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.bot import app, db_client, cron_client, birthday_service, knowledge_service, chat_manager, user_repo, vector_client
from slack_bolt.adapter.socket_mode import SocketModeHandler
import asyncio
from src.core.logger import logger
from dotenv import load_dotenv

# Global handler değişkeni (shutdown için)
handler = None
shutdown_in_progress = False

def graceful_shutdown(signum=None, frame=None):
    """Graceful shutdown işlemini gerçekleştirir."""
    global handler, shutdown_in_progress
    
    if shutdown_in_progress:
        logger.warning("[!] Shutdown zaten devam ediyor, zorla kapatılıyor...")
        sys.exit(1)
    
    shutdown_in_progress = True
    
    print("\n" + "="*60)
    print("           CEMIL BOT - GRACEFUL SHUTDOWN")
    print("="*60 + "\n")
    
    logger.info("[>] Graceful shutdown başlatılıyor...")
    
    try:
        # 1. SocketModeHandler'ı durdur
        if handler:
            logger.info("[>] Slack bağlantısı kapatılıyor...")
            try:
                # SocketModeHandler thread-based çalışır
                # Handler'ın thread'ini durdur (eğer varsa)
                if hasattr(handler, 'stop'):
                    handler.stop()
                elif hasattr(handler, 'close'):
                    handler.close()
                # WebSocket client'ını kapat
                if hasattr(handler, 'client') and hasattr(handler.client, 'close'):
                    handler.client.close()
                logger.info("[+] Slack bağlantısı kapatıldı.")
            except Exception as e:
                logger.warning(f"[!] Slack bağlantısı kapatılırken hata: {e}")
        
        # 2. Cron scheduler'ı durdur
        logger.info("[>] Zamanlayıcılar durduruluyor...")
        try:
            cron_client.shutdown(wait=True)
            logger.info("[+] Zamanlayıcılar durduruldu.")
        except Exception as e:
            logger.warning(f"[!] Zamanlayıcılar durdurulurken hata: {e}")
        
        # 3. Veritabanı bağlantılarını kapat (SQLite otomatik kapanır ama yine de kontrol edelim)
        logger.info("[>] Veritabanı bağlantıları kapatılıyor...")
        # SQLite connection'lar context manager ile otomatik kapanır
        logger.info("[+] Veritabanı bağlantıları temizlendi.")
        
        logger.info("[+] Graceful shutdown tamamlandı. Görüşmek üzere! 👋")
        print("\n[+] Bot başarıyla kapatıldı. Görüşmek üzere! 👋\n")
        
    except Exception as e:
        logger.error(f"[X] Shutdown sırasında hata: {e}")
        print(f"\n[X] Shutdown sırasında hata oluştu: {e}\n")
    finally:
        sys.exit(0)

def main():
    """Cemil Bot'u başlatan ana fonksiyon."""
    global handler
    
    load_dotenv()
    
    # Signal handler'ları kaydet
    signal.signal(signal.SIGINT, graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)
    
    # Ayrıca atexit ile de kaydet (program normal sonlanırsa)
    atexit.register(graceful_shutdown)
    
    # Kritik environment variable kontrolü
    required_vars = ["SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "GROQ_API_KEY"]
    missing_vars = [var for var in required_vars if not os.environ.get(var)]
    
    if missing_vars:
        logger.error(f"[X] Eksik environment variables: {', '.join(missing_vars)}")
        logger.error("[X] Lütfen .env dosyasını kontrol edin!")
        return
    
    print("\n" + "="*60)
    print("           CEMIL BOT - HIZLI BAŞLATMA (PROD)")
    print("="*60 + "\n")

    # 1. Veritabanı
    logger.info("[>] Veritabanı kontrol ediliyor...")
    db_client.init_db()
    
    # --- CSV Veri İçe Aktarma Kontrolü ---
    # Klasörlerin varlığını kontrol et
    os.makedirs("data", exist_ok=True)
    os.makedirs("knowledge_base", exist_ok=True)
    os.makedirs("logs", exist_ok=True)
    
    CSV_PATH = "data/initial_users.csv"
    
    if not os.path.exists(CSV_PATH):
        # Şablon dosya oluştur
        print(f"\n[i] '{CSV_PATH}' dosyası bulunamadı. Şablon oluşturuluyor...")
        try:
            with open(CSV_PATH, 'w', encoding='utf-8') as f:
                f.write("Slack ID,First Name,Surname,Full Name,Birthday,Cohort\n")
                f.write("U12345,Ahmet,Yilmaz,Ahmet Yilmaz,01.01.1990,Yapay Zeka\n")
            print(f"[+] Şablon oluşturuldu: {CSV_PATH}")
            print(f"[i] Not: Şablon içinde örnek veri bulunmaktadır.")
            choice = input("Bu şablonu şimdi kullanmak ister misiniz? (e/h): ").lower().strip()
            
            if choice == 'e':
                print("[i] Veriler işleniyor...")
                try:
                    count = user_repo.import_from_csv(CSV_PATH)
                    print(f"[+] Başarılı! {count} kullanıcı eklendi.")
                except Exception as e:
                    logger.error(f"[X] Import hatası: {e}")
                    print("Hata oluştu, logları kontrol edin.")
            else:
                print("[i] Şablon atlandı. Dosyayı doldurup botu yeniden başlattığınızda kullanabilirsiniz.")
        except Exception as e:
            logger.error(f"[X] Şablon oluşturma hatası: {e}")
    else:
        # Dosya var, kullanıp kullanmayacağını sor
        print(f"\n[?] '{CSV_PATH}' dosyası bulundu.")
        choice = input("Bu CSV dosyasındaki verileri kullanmak ister misiniz? (e/h): ").lower().strip()
        
        if choice == 'e':
            print("[i] Veriler işleniyor...")
            try:
                count = user_repo.import_from_csv(CSV_PATH)
                print(f"[+] Başarılı! {count} kullanıcı eklendi.")
            except Exception as e:
                logger.error(f"[X] Import hatası: {e}")
                print("Hata oluştu, logları kontrol edin.")
        else:
            print("[i] CSV dosyası atlandı, mevcut veritabanı ile devam ediliyor.")
    # -------------------------------------

    # 2. Cron
    logger.info("[>] Zamanlayıcılar başlatılıyor...")
    cron_client.start()
    birthday_service.schedule_daily_check(hour=9, minute=0)

    # 3. Vektör Veritabanı Kontrolü
    VECTOR_INDEX_PATH = "data/vector_store.index"
    VECTOR_PKL_PATH = "data/vector_store.pkl"
    
    vector_index_exists = os.path.exists(VECTOR_INDEX_PATH) and os.path.exists(VECTOR_PKL_PATH)
    
    if vector_index_exists:
        # Mevcut veriler var
        print(f"\n[?] Vektör veritabanı bulundu (mevcut veriler: {len(vector_client.documents) if vector_client.documents else 0} parça).")
        choice = input("Vektör veritabanını yeniden oluşturmak ister misiniz? (e/h): ").lower().strip()
        
        if choice == 'e':
            print("[i] Vektör veritabanı yeniden oluşturuluyor...")
            logger.info("[>] Bilgi Küpü indeksleniyor...")
            asyncio.run(knowledge_service.process_knowledge_base())
            print("[+] Vektör veritabanı başarıyla güncellendi.")
        else:
            print("[i] Mevcut vektör veritabanı kullanılıyor.")
            logger.info("[i] Mevcut vektör veritabanı yüklendi.")
    else:
        # Vektör veritabanı yok, oluştur
        print(f"\n[i] Vektör veritabanı bulunamadı. Oluşturuluyor...")
        logger.info("[>] Bilgi Küpü indeksleniyor...")
        asyncio.run(knowledge_service.process_knowledge_base())
        print("[+] Vektör veritabanı başarıyla oluşturuldu.")

    # 4. Slack
    app_token = os.environ.get("SLACK_APP_TOKEN")
    if not app_token:
        logger.error("[X] SLACK_APP_TOKEN eksik!")
        return

    logger.info("[>] Slack Bağlantısı kuruluyor...")
    
    # Başlangıç Mesajı Kontrolü
    startup_channel = os.environ.get("SLACK_STARTUP_CHANNEL")
    github_repo = os.environ.get("GITHUB_REPO")
    
    if startup_channel:
        print(f"\n[?] Başlangıç kanalı bulundu: {startup_channel}")
        choice = input("Başlangıç mesajı (welcome) gönderilsin mi? (e/h): ").lower().strip()
        
        if choice == 'e':
            try:
                startup_text = (
                    "👋 *Merhabalar! Ben Cemil, göreve hazırım!* ☀️\n\n"
                    "Topluluk etkileşimini artırmak ve işlerinizi kolaylaştırmak için buradayım.\n"
                    "İşte yapabildiklerim:\n\n"
                    "☕ *`/kahve`* - Kahve molası eşleşmesi için havuza katıl.\n"
                    "🗳️ *`/oylama`* - Hızlı ve demokratik anketler başlat (Admin).\n"
                    "📝 *`/geri-bildirim`* - Akademi ekibine anonim olarak fikir/önerilerini ilet.\n"
                    "🧠 *`/sor`* - Akademi dökümanları ile oluşturulan bilgi havuzuna soru sor.\n"
                    "👤 *`/profilim`* - Sistemdeki kayıtlı bilgilerini görüntüle.\n\n"
                    "Güzel bir gün dilerim! ✨"
                )
                
                if github_repo:
                    startup_text += f"\n\n📚 Kaynak kod: {github_repo}"
                
                startup_blocks = [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": startup_text + "\n<!channel>"
                        }
                    }
                ]

                chat_manager.post_message(
                    channel=startup_channel,
                    text="👋 Merhabalar! Ben Cemil, göreve hazırım!",
                    blocks=startup_blocks
                )
                logger.info(f"[+] Başlangıç mesajı gönderildi: {startup_channel}")
                print(f"[+] Başlangıç mesajı gönderildi: {startup_channel}")
            except Exception as e:
                logger.error(f"[X] Başlangıç mesajı gönderilemedi: {e}")
                print(f"[X] Başlangıç mesajı gönderilemedi: {e}")
        else:
            print("[i] Başlangıç mesajı atlandı.")
            logger.info("[i] Başlangıç mesajı kullanıcı tarafından atlandı.")
    else:
        print("[i] SLACK_STARTUP_CHANNEL tanımlı değil, başlangıç mesajı gönderilmeyecek.")

    print("\n" + "="*60)
    print("           BOT ÇALIŞIYOR - CTRL+C ile durdurun")
    print("="*60 + "\n")

    handler = SocketModeHandler(app, app_token)
    
    try:
        handler.start()
    except KeyboardInterrupt:
        # Ctrl+C yakalandı, graceful shutdown çağrılacak
        logger.info("[i] KeyboardInterrupt yakalandı, graceful shutdown başlatılıyor...")
        graceful_shutdown()
    except Exception as e:
        logger.error(f"[X] Bot başlatılırken hata: {e}")
        graceful_shutdown()

if __name__ == "__main__":
    main()
