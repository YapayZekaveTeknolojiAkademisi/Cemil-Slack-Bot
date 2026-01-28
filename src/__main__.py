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

from src.bot import app, db_client, cron_client, knowledge_service, chat_manager, user_repo, vector_client
from slack_bolt.adapter.socket_mode import SocketModeHandler
import asyncio
from src.core.logger import logger
from src.core.settings import get_settings
from dotenv import load_dotenv

def ensure_database_schema():
    """
    Veritabanı şemasının güncel olduğundan emin olur.
    Eksik kolonları otomatik ekler, gereksiz kolonları temizler.
    """
    try:
        logger.info("[>] Veritabanı şema kontrolü yapılıyor...")
        conn = db_client.get_connection()
        cursor = conn.cursor()
        
        # challenge_hubs tablosundaki kolonları kontrol et
        cursor.execute("PRAGMA table_info(challenge_hubs)")
        cols = {row["name"] for row in cursor.fetchall()}
        
        # Gerekli yeni kolonlar - eksikse ekle
        migrations = []
        if "project_name" not in cols:
            migrations.append("ALTER TABLE challenge_hubs ADD COLUMN project_name TEXT;")
        if "project_description" not in cols:
            migrations.append("ALTER TABLE challenge_hubs ADD COLUMN project_description TEXT;")
        if "summary_message_ts" not in cols:
            migrations.append("ALTER TABLE challenge_hubs ADD COLUMN summary_message_ts TEXT;")
        if "summary_message_channel_id" not in cols:
            migrations.append("ALTER TABLE challenge_hubs ADD COLUMN summary_message_channel_id TEXT;")
        if "ended_at" not in cols:
            migrations.append("ALTER TABLE challenge_hubs ADD COLUMN ended_at TIMESTAMP;")
        
        # challenge_evaluations tablosundaki kolonları kontrol et
        cursor.execute("PRAGMA table_info(challenge_evaluations)")
        eval_cols = {row["name"] for row in cursor.fetchall()}
        
        if "jury_status" not in eval_cols:
            migrations.append("ALTER TABLE challenge_evaluations ADD COLUMN jury_status TEXT DEFAULT 'recruiting';")
            logger.info("[i] jury_status kolonu eksik, eklenecek...")
        
        # Gereksiz kolonları temizle (canvas_id - kodda kullanılmıyor, summary_message_ts kullanılıyor)
        if "canvas_id" in cols:
            logger.info("[i] Gereksiz canvas_id kolonu tespit edildi (summary_message_ts kullanılıyor). Kaldırılıyor...")
            try:
                # SQLite'da kolon kaldırmak için tabloyu yeniden oluşturmak gerekir
                # Önce mevcut verileri yedekle
                cursor.execute("SELECT * FROM challenge_hubs")
                rows = cursor.fetchall()
                col_names = [desc[0] for desc in cursor.description]
                
                # canvas_id hariç kolonları al
                new_cols = [col for col in col_names if col != "canvas_id"]
                new_cols_str = ", ".join(new_cols)
                
                # Yeni tablo oluştur (canvas_id olmadan)
                cursor.execute("""
                    CREATE TABLE challenge_hubs_new (
                        id TEXT PRIMARY KEY,
                        creator_id TEXT NOT NULL,
                        theme TEXT NOT NULL,
                        team_size INTEGER NOT NULL,
                        status TEXT DEFAULT 'recruiting',
                        challenge_channel_id TEXT,
                        hub_channel_id TEXT,
                        selected_project_id TEXT,
                        llm_customizations TEXT,
                        deadline_hours INTEGER DEFAULT 48,
                        difficulty TEXT DEFAULT 'intermediate',
                        deadline TIMESTAMP,
                        started_at TIMESTAMP,
                        completed_at TIMESTAMP,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        project_name TEXT,
                        project_description TEXT,
                        summary_message_ts TEXT,
                        summary_message_channel_id TEXT,
                        ended_at TIMESTAMP,
                        FOREIGN KEY (creator_id) REFERENCES users(slack_id) ON DELETE CASCADE
                    )
                """)
                
                # Verileri kopyala (canvas_id hariç)
                if rows:
                    placeholders = ", ".join(["?"] * len(new_cols))
                    for row in rows:
                        row_dict = dict(zip(col_names, row))
                        values = [row_dict[col] for col in new_cols]
                        cursor.execute(f"INSERT INTO challenge_hubs_new ({new_cols_str}) VALUES ({placeholders})", values)
                
                # Eski tabloyu sil ve yenisini yeniden adlandır
                cursor.execute("DROP TABLE challenge_hubs")
                cursor.execute("ALTER TABLE challenge_hubs_new RENAME TO challenge_hubs")
                
                # Index'leri yeniden oluştur
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_challenge_hubs_status ON challenge_hubs(status)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_challenge_hubs_creator ON challenge_hubs(creator_id)")
                
                logger.info("[+] Gereksiz canvas_id kolonu kaldırıldı.")
            except Exception as e:
                logger.warning(f"[!] canvas_id kolonu kaldırılırken hata (devam ediliyor): {e}")
        
        # Eksik kolonları ekle
        for migration in migrations:
            cursor.execute(migration)
            logger.info(f"[+] Şema güncellendi: {migration.split('ADD COLUMN')[1].strip()}")
        
        if migrations or "canvas_id" in cols:
            conn.commit()
            logger.info("[+] Veritabanı şeması güncellendi.")
        else:
            logger.info("[+] Veritabanı şeması güncel.")
        
        conn.close()
    except Exception as e:
        logger.error(f"[X] Şema kontrolü sırasında hata: {e}", exc_info=True)

# Non-interactive mod (CI / prod deploy) için flag
NON_INTERACTIVE = os.environ.get("CEMIL_NON_INTERACTIVE") == "1"

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
    
    # Settings kontrolü - .env dosyası yüklendikten sonra yeniden yükle
    try:
        # bot.py'de import edilirken settings oluşturulmuş olabilir, .env yüklendikten sonra yeniden yükle
        settings = get_settings(reload=True)  # .env yüklendikten sonra yeniden yükle
        logger.info(f"[i] Settings yüklendi - Startup Channel: {settings.startup_channel or 'Tanımlı değil'}")
    except Exception as e:
        logger.error(f"[X] Konfigürasyon yükleme hatası: {e}")
        logger.error("[X] Lütfen .env dosyasını kontrol edin!")
        return
    
    print("\n" + "="*60)
    print("           CEMIL BOT - HIZLI BAŞLATMA (PROD)")
    print("="*60 + "\n")

    # 1. Veritabanı
    logger.info("[>] Veritabanı kontrol ediliyor...")
    db_client.init_db()
    
    # Şema güncellemelerini uygula (yeni kolonlar varsa ekle)
    ensure_database_schema()
    
    # Challenge tablolarını temizle (startup'ta) - Settings'e bağlı
    if settings.db_clean_on_startup:
        logger.info("[>] Challenge tabloları TEMİZLENİYOR (Settings gereği)...")
        deleted_counts = db_client.clean_challenge_tables()
        if deleted_counts:
            total = sum(deleted_counts.values())
            print(f"[+] Challenge tabloları temizlendi: {total} kayıt silindi")
        else:
            print("[i] Challenge tabloları zaten temizdi.")
    else:
        logger.info("[i] Challenge tabloları temizlenmedi (Settings: False).")
    
    # --- CSV Veri İçe Aktarma Kontrolü ---
    # Klasörlerin varlığını kontrol et
    os.makedirs("data", exist_ok=True)
    os.makedirs(settings.knowledge_base_path, exist_ok=True)
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
        except Exception as e:
            logger.error(f"[X] Şablon oluşturma hatası: {e}")
    else:
        # Dosya var → her zaman otomatik içe aktar
        print(f"\n[?] '{CSV_PATH}' dosyası bulundu.")
        print("[i] CSV verileri otomatik içe aktarılıyor...")
        try:
            count = user_repo.import_from_csv(CSV_PATH)
            print(f"[+] Başarılı! {count} kullanıcı eklendi.")
        except Exception as e:
            logger.error(f"[X] Import hatası: {e}")
            print("Hata oluştu, logları kontrol edin.")
    # -------------------------------------

    # 2. Cron
    logger.info("[>] Zamanlayıcılar başlatılıyor...")
    cron_client.start()

    # 3. Vektör Veritabanı Kontrolü
    vector_index_exists = os.path.exists(settings.vector_store_path) and os.path.exists(settings.vector_store_pkl_path)
    
    if vector_index_exists:
        # Mevcut veriler var
        print(f"\n[?] Vektör veritabanı bulundu (mevcut veriler: {len(vector_client.documents) if vector_client.documents else 0} parça).")
        
        if settings.kb_rebuild_index:
            print("[i] Vektör veritabanı yeniden oluşturuluyor (Settings gereği)...")
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
    if not settings.slack_app_token:
        logger.error("[X] SLACK_APP_TOKEN eksik!")
        return

    logger.info("[>] Slack Bağlantısı kuruluyor...")
    
    # Başlangıç Mesajı Kontrolü - Settings'i yeniden yükle (.env güncellenmiş olabilir)
    # bot.py import edilirken settings oluşturulmuş olabilir, .env yüklendikten sonra yeniden yükle
    settings = get_settings(reload=True)
    startup_channel = settings.startup_channel
    github_repo = settings.github_repo
    
    logger.info(f"[i] Startup channel kontrolü: {startup_channel or 'Tanımlı değil'}")
    
    print("\n" + "="*60)
    print("           BAŞLANGIÇ MESAJI AYARLARI")
    print("="*60)
    
    if startup_channel:
        print(f"\n[✓] Başlangıç kanalı: {startup_channel}")
        if settings.slack_send_welcome_message:
            print(f"    [>] Başlangıç mesajı GÖNDERİLİYOR (Settings: True)...")
            try:
                startup_text = (
                    "👋 *Merhabalar! Ben Cemil, Yapay Zeka Akademisi'nin yardımcı asistanıyım!* ☀️\n\n"
                    "Topluluk etkileşimini artırmak, öğrenmeyi desteklemek ve işlerinizi kolaylaştırmak için buradayım.\n\n"
                    "🎯 *Ana Özelliklerim:*\n\n"
                    
                    "☕ *Kahve Eşleşmesi*\n"
                    "• *Komut:* `/kahve`\n"
                    "• *Nasıl Kullanılır:* Komutu çalıştırın, başka biri de kahve isterse otomatik eşleşirsiniz.\n"
                    "• *Ne Olur:* Özel bir kanal açılır, 5 dakika sohbet edebilirsiniz. Sonra kanal kapanır ve sohbet özeti DM'inize gelir.\n\n"
                    
                    "🆘 *Yardım Sistemi*\n"
                    "• *Komut:* `/yardim-iste <konu> <açıklama>`\n"
                    "• *Nasıl Kullanılır:* Yardıma ihtiyacınız olduğunda komutu kullanın.\n"
                    "• *Ne Olur:* Yeni bir yardım kanalı açılır, topluluk üyeleri 'Yardım Et' butonuna tıklayarak katılabilir. Kanal 10 dakika sonra otomatik kapanır ve özet gönderilir.\n\n"
                    
                    "🚀 *Challenge Hub (Mini Hackathon)*\n"
                    "• *Nasıl Başlar?* `/challenge start <takım_büyüklüğü>` (örn: `/challenge start 4`) komutu ile bir challenge başlatırsın.\n"
                    "  - Cemil senin adına #challenge-hub'da bir ilan açar ve \"Challenge'a Katıl\" butonu ekler.\n"
                    "  - Diğer bursiyerler butona tıklayarak veya `/challenge join` yazarak takıma katılabilir.\n"
                    "• *Takım Nasıl Oluşur?* Takım boyutu (sen + katılımcılar) dolduğunda:\n"
                    "  - Uygun temadan rastgele bir proje seçilir.\n"
                    "  - Sadece takım için özel bir *challenge kanalı* açılır.\n"
                    "  - Proje açıklaması, görevler, teslim edilecekler ve süre bu kanala detaylı bir mesaj olarak gönderilir.\n"
                    "• *Challenge Süreci:*\n"
                    "  - Belirlenen süre boyunca bu kanalda birlikte çalışırsınız (min 72 saatlik süre uygulanır).\n"
                    "  - Kanal kuralları ve ipuçları ilk mesajlarda detaylıca anlatılır.\n"
                    "• *Challenge Nasıl Biter?*\n"
                    "  - Süre dolunca Cemil challenge'ı otomatik tamamlar, kanal arşivlenir.\n"
                    "  - İsterseniz daha erken bitirmek için challenge kanalında \"bitir / finish / done\" yazabilirsiniz.\n"
                    "• *Değerlendirme (Voting) Nasıl Çalışır?*\n"
                    "  - Challenge tamamlandığında challenge kanalına \"📊 Projeyi Değerlendir\" butonu gelir.\n"
                    "  - Bu butona basan en fazla 3 kişi için ayrı bir *değerlendirme kanalı* açılır (48 saat açık kalır).\n"
                    "  - Değerlendirme kanalında:\n"
                    "    • `/challenge set True` → Proje başarılı\n"
                    "    • `/challenge set False` → Proje başarısız\n"
                    "    • `/challenge set github <link>` → Projenin GitHub reposu (public olmalı)\n"
                    "  - Challenge'ın *başarılı* sayılması için:\n"
                    "    • True oyları, False oylarından fazla olmalı ve\n"
                    "    • 48 saat içinde public bir GitHub linki eklenmiş olmalı.\n"
                    "• *Admin Komutları:*\n"
                    "  - `/admin-basarili-projeler` → Başarılı challenge'ları, ekipleri ve GitHub linklerini listeler.\n"
                    "  - `/admin-istatistik` → Genel kullanım ve challenge istatistiklerini gösterir.\n\n"
                    
                    "🧠 *Bilgi Küpü (RAG Sistemi)*\n"
                    "• *Komut:* `/sor <soru>`\n"
                    "• *Nasıl Kullanılır:* Akademi dökümanları hakkında soru sorun.\n"
                    "• *Ne Olur:* Bilgi küpündeki PDF'lerden ilgili bilgiler bulunur ve Türkçe cevap verilir.\n\n"
                    
                    "🗳️ *Oylama Sistemi* (Admin)\n"
                    "• *Komut:* `/oylama <konu> <seçenek1> <seçenek2> ...`\n"
                    "• *Nasıl Kullanılır:* Admin olarak anket başlatın, herkes oy verir.\n"
                    "• *Ne Olur:* Anket mesajı gönderilir, kullanıcılar butonlara tıklayarak oy verir. Sonuçlar otomatik hesaplanır.\n\n"
                    
                    "📝 *Geri Bildirim Sistemi*\n"
                    "• *Komut:* `/geri-bildirim <mesaj>`\n"
                    "• *Nasıl Kullanılır:* Anonim olarak fikir, öneri veya şikayet gönderin.\n"
                    "• *Ne Olur:* Mesajınız admin kanalına anonim olarak iletilir.\n\n"
                    
                    "👤 *Profil Görüntüleme*\n"
                    "• *Komut:* `/profilim`\n"
                    "• *Nasıl Kullanılır:* Sistemdeki kayıtlı bilgilerinizi görüntüleyin.\n\n"
                    
                    "📊 *Admin İstatistikleri* (Admin)\n"
                    "• *Komut:* `/admin-istatistik` - Genel bot kullanım istatistiklerini görüntüle\n"
                    "• *Komut:* `/admin-basarili-projeler` - Başarılı challenge projelerini, ekipleri ve GitHub linklerini görüntüle\n\n"
                    
                    "🏥 *Bot Sağlık Kontrolü*\n"
                    "• *Komut:* `/cemil-health`\n"
                    "• *Nasıl Kullanılır:* Bot'un çalışma durumunu kontrol edin.\n\n"
                    
                    "💡 *İpuçları:*\n"
                    "• Tüm komutlar için `/help` yazabilirsiniz (yakında)\n"
                    "• Challenge'lar için takım çalışması ve öğrenme odaklıdır\n"
                    "• Yardım ve kahve kanalları otomatik kapanır, özetler DM'inize gelir\n"
                    "• Bilgi küpü sadece Türkçe cevap verir\n\n"
                    
                    "Güzel bir gün dilerim! ✨"
                )
                
                if github_repo and "SİZİN_KULLANICI_ADINIZ" not in github_repo:
                    startup_text += f"\n\n📚 *Kaynaklar:*\n"
                    startup_text += f"• <{github_repo}/blob/main/README.md|Kullanım Kılavuzu>\n"
                    startup_text += f"• <{github_repo}/blob/main/CHANGELOG.md|Neler Yeni?>\n"
                    startup_text += f"• <{github_repo}/blob/main/CONTRIBUTING.md|Katkıda Bulun>"
                
                startup_blocks = [
                                    {
                                        "type": "header",
                                        "text": {
                                            "type": "plain_text",
                                            "text": "👋 Merhabalar! Ben Cemil, Yapay Zeka Akademisi'nin yardımcı asistanıyım!",
                                            "emoji": True
                                        }
                                    },
                                    {
                                        "type": "section",
                                        "text": {
                                            "type": "mrkdwn",
                                            "text": "Topluluk etkileşimini artırmak, öğrenmeyi desteklemek ve işlerinizi kolaylaştırmak için buradayım. Aşağıda tüm özelliklerim ve nasıl kullanılacağı detaylıca açıklanmıştır."
                                        }
                                    },
                                    {"type": "divider"},
                                    {
                                        "type": "header",
                                        "text": {
                                            "type": "plain_text",
                                            "text": "🎯 Ana Özellikler",
                                            "emoji": True
                                        }
                                    },
                                    {
                                        "type": "section",
                                        "fields": [
                                            {
                                                "type": "mrkdwn",
                                                "text": "*☕ Kahve Eşleşmesi*\n*Komut:* `/kahve`\n*Kullanım:* Komutu çalıştırın, başka biri de kahve isterse otomatik eşleşirsiniz."
                                            },
                                            {
                                                "type": "mrkdwn",
                                                "text": "*🆘 Yardım Sistemi*\n*Komut:* `/yardim-iste <konu> <açıklama>`\n*Kullanım:* Yardıma ihtiyacınız olduğunda komutu kullanın."
                                            }
                                        ]
                                    },
                                    {
                                        "type": "section",
                                        "fields": [
                                            {
                                                "type": "mrkdwn",
                                                "text": "*🚀 Challenge Hub*\n*Komut:* `/challenge start <takım>`\n*Kullanım:* Challenge başlatın, diğerleri katılır."
                                            },
                                            {
                                                "type": "mrkdwn",
                                                "text": "*🧠 Bilgi Küpü (RAG)*\n*Komut:* `/sor <soru>`\n*Kullanım:* Akademi dökümanları hakkında soru sorun."
                                            }
                                        ]
                                    },
                                    {
                                        "type": "section",
                                        "fields": [
                                            {
                                                "type": "mrkdwn",
                                                "text": "*🗳️ Oylama* (Admin)\n*Komut:* `/oylama <konu> <seçenekler>`\n*Kullanım:* Admin olarak anket başlatın."
                                            },
                                            {
                                                "type": "mrkdwn",
                                                "text": "*📝 Geri Bildirim*\n*Komut:* `/geri-bildirim <mesaj>`\n*Kullanım:* Anonim fikir/öneri gönderin."
                                            }
                                        ]
                                    },
                                    {
                                        "type": "section",
                                        "fields": [
                                            {
                                                "type": "mrkdwn",
                                                "text": "*👤 Profil*\n*Komut:* `/profilim`\n*Kullanım:* Kayıtlı bilgilerinizi görüntüleyin."
                                            },
                                            {
                                                "type": "mrkdwn",
                                                "text": "*📊 Admin İstatistik*\n*Komut:* `/admin-istatistik` | `/admin-basarili-projeler`"
                                            }
                                        ]
                                    },
                                    {
                                        "type": "section",
                                        "fields": [
                                            {
                                                "type": "mrkdwn",
                                                "text": "*📅 Günlük Pratik & Motivasyon*\n*Komut:* `/daily english` | `/daily motivasyon`\n*Kullanım:* İngilizce pratik veya motivasyon mesajı alırsınız."
                                            },
                                            {
                                                "type": "mrkdwn",
                                                "text": "*🏥 Bot Sağlık*\n*Komut:* `/cemil-health`\n*Kullanım:* Bot'un çalışma durumunu kontrol edin."
                                            }
                                        ]
                                    },
                                    {"type": "divider"},
                                    {
                                        "type": "section",
                                        "text": {
                                            "type": "mrkdwn",
                                            "text": "*💡 İpuçları:*\n• Challenge'lar takım çalışması odaklıdır\n• Yardım ve kahve kanalları otomatik kapanır\n• Bilgi küpü sadece Türkçe cevap verir"
                                        }
                                    },
                                    {
                                        "type": "context",
                                        "elements": [
                                            {
                                                "type": "mrkdwn",
                                                "text": "Güzel bir gün dilerim! ✨ <!channel>"
                                            }
                                        ]
                                    }
                                ]

                chat_manager.post_message(
                    channel=startup_channel,
                    text="👋 Merhabalar! Ben Cemil, Yapay Zeka Akademisi'nin yardımcı asistanıyım!",
                    blocks=startup_blocks,
                    unfurl_links=True,
                    unfurl_media=True
                )
                logger.info(f"[+] Başlangıç mesajı gönderildi: {startup_channel}")
                print(f"[+] Başlangıç mesajı gönderildi: {startup_channel}")
            except Exception as e:
                logger.error(f"[X] Başlangıç mesajı gönderilemedi: {e}")
                print(f"[X] Başlangıç mesajı gönderilemedi: {e}")
        else:
            print("[i] Başlangıç mesajı GÖNDERİLMEDİ (Settings: False).")
    else:
        print("[i] SLACK_STARTUP_CHANNEL tanımlı değil, başlangıç mesajı atlandı.")

    print("\n" + "="*60)
    print("           BOT ÇALIŞIYOR - CTRL+C ile durdurun")
    print("="*60 + "\n")

    # Slack token kontrolü
    if not settings.slack_app_token:
        logger.error("[X] SLACK_APP_TOKEN eksik! Bot başlatılamaz.")
        print("[X] SLACK_APP_TOKEN eksik! Lütfen .env dosyasını kontrol edin.")
        return
    
    logger.info("[>] Slack Socket Mode handler başlatılıyor...")
    print("[i] Slack bağlantısı kuruluyor...")
    
    handler = SocketModeHandler(app, settings.slack_app_token)
    
    try:
        logger.info("[>] Handler.start() çağrılıyor...")
        handler.start()
        logger.info("[+] Slack Socket Mode başarıyla başlatıldı!")
        print("[+] Bot Slack'e bağlandı ve komutları dinliyor!")
    except KeyboardInterrupt:
        # Ctrl+C yakalandı, graceful shutdown çağrılacak
        logger.info("[i] KeyboardInterrupt yakalandı, graceful shutdown başlatılıyor...")
        graceful_shutdown()
    except Exception as e:
        logger.error(f"[X] Bot başlatılırken hata: {e}", exc_info=True)
        print(f"[X] Bot başlatılırken hata: {e}")
        print("[i] Lütfen log dosyasını kontrol edin: logs/cemil_detailed.log")
        graceful_shutdown()

if __name__ == "__main__":
    main()
