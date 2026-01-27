-- Migration: Gereksiz canvas_id kolonunu kaldır
-- Date: 2026-01-27
-- Description: challenge_hubs tablosundaki canvas_id kolonu kodda kullanılmıyor.
--              summary_message_ts ve summary_message_channel_id kullanılıyor.
--              Bu kolon gereksiz olduğu için kaldırılıyor.

-- NOT: SQLite'da kolon kaldırmak için tabloyu yeniden oluşturmak gerekir.
-- Bu migration __main__.py'deki ensure_database_schema() fonksiyonu tarafından
-- otomatik olarak uygulanacaktır.

-- Manuel uygulama için:
-- 1. Mevcut verileri yedekle
-- 2. Yeni tablo oluştur (canvas_id olmadan)
-- 3. Verileri kopyala
-- 4. Eski tabloyu sil
-- 5. Yeni tabloyu yeniden adlandır
-- 6. Index'leri yeniden oluştur
