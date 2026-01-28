import asyncio
import random
import time
from datetime import datetime
from slack_bolt import App
from src.core.logger import logger
from src.clients import GroqClient
from src.commands import ChatManager

#* --- ALIASES (Takma Adlar) ---
ALIASES = {
    "english": "english",
    "eng": "english",
    "ingilizce": "english",
    "ing": "english",
    
    "motivasyon": "motivasyon",
    "motivation": "motivasyon",
    "moti": "motivasyon"
}

#* --- CONFIGURATIONS ---
DAILY_CONFIGS = {
    "english": {
        "system_prompt": 
        """You are the Coordinator for an English Conversation Club. 
        Your goal is to create a clean, minimalist, and social Slack card.

        STRICT FORMATTING RULES:
        - Use ONLY '*' for bold. (e.g. *Bold Text*)
        - DO NOT use any Markdown headings (#), lists (-), or code blocks.
        - Use EXACTLY ONE blank line between sections.
        - Output ONLY the card content. No intro/outro sentences.

        CARD STRUCTURE:
        1) *Month Day, Year*
        (Blank line)
        2) *Topic:* <Social Daily Activity>
        <A short 2-3 sentence paragraph about this social topic.>
        (Blank line)
        3) *Vocabulary:*
        *Word*: <word>
        *Translation*: <Turkish>
        *Example*: <A short, simple sentence>
        (Blank line)
        *Word*: <word>
        *Translation*: <Turkish>
        *Example*: <A short, simple sentence>
        (Blank line)
        4) *Discussion Questions:*
        Q1: <Question>
        Q2: <Question>
        Q3: <Question>

        CONTENT CONSTRAINTS:
        - TOPIC: Must be 100% social/daily. (e.g., Making coffee, rainy weather, walking in the park, supermarket, pets).
        - FORBIDDEN: Anything about "Office", "Home Office", "Productivity", "Work", or "Marketing". 
        - VOCABULARY: Exactly 2 words related to the topic.""",
        
        "title": "English Conversation Club",
        "color": "#4A90E2"
    },

    "motivasyon": {
        "system_prompt": """Sen bir motivasyon koçusun. Günün modunu yükseltecek, kısa ve etkili bir motivasyon mesajı ver.""",
        "title": "🚀 Günün Motivasyon Cümlesi!",
        "color": "#F5A623"
    }
}

#* --- COOLDOWN STORAGE ---
# Yapı: { "user_id": { "english": timestamp, "motivasyon": timestamp } }
DAILY_COOLDOWN_STORAGE = {}

def setup_daily_handlers(app: App, groq_client: GroqClient, chat_manager: ChatManager):
    
    @app.command("/daily")
    def handle_daily_command(ack, body, respond, say):
        ack()
        user_id = body["user_id"]

        # 1. Kullanıcının yazdığı ham metni alıyoruz temizliyoruz ve Alias Kontrolü yapıyoruz
        raw_text = body.get("text", "").strip().lower()

        # ALIASES içinden raw_text'i arıyoruz, bulamazsak None döner.
        user_text = ALIASES.get(raw_text) # Yazılan kelime havuzda var mı?

        # 2. VALIDATION (Geçerli bir komut mu?)
        if not user_text or user_text not in DAILY_CONFIGS:
            available = ", ".join([k.capitalize() for k in DAILY_CONFIGS.keys()])
            respond(text=f"⚠️ Geçersiz komut! Şunları deneyebilirsin: `{available}`", response_type="ephemeral")
            return

        # 3. COOLDOWN KONTROLÜ (Kullanıcı ve komut bazlı)
        now = time.time()
        # Kullanıcının geçmişini al, yoksa boş sözlük dön
        user_history = DAILY_COOLDOWN_STORAGE.get(user_id, {})
        # Bu spesifik komutun (english/motivasyon) son kullanımını al
        last_use = user_history.get(user_text, 0)

        if (now - last_use) < 600: # 10 dakika olarak tanımlı
            remaining = int(600 - (now - last_use))
            respond(text=f"⏳ Sakin şampiyon! {user_text.capitalize()} için {remaining // 60} dk {remaining % 60} sn sonra tekrar dene.", response_type="ephemeral")
            return

        # 4. HAZIRLIK VE AI ÇAĞRISI
        config = DAILY_CONFIGS[user_text]
        respond(text=f"{config['title']} hazırlanıyor...", response_type="ephemeral")

        async def process_daily():
            try:
                # 1. DAHA GÜÇLÜ RANDOMİZASYON
                # Sadece 4 haneli sayı değil, mikrosaniyeyi de işin içine katalım
                unique_id = int(time.time() * 1000) % 100000 
                current_date_str = datetime.now().strftime("%d %B %Y, %H:%M")
                
                # 2. DİNAMİK USER PROMPT (AI'yı zorluyoruz)
                # AI'ya her seferinde tamamen farklı bir alt konu seçmesini emrediyoruz AI'nın seçebileceği geniş bir tema havuzu oluşturuyoruz
                themes = [
                    "Space & Astronomy", "Cooking & Spices", "Ocean Life", "Ancient Civilizations",
                    "Weekend Traditions", "Gardening Tips", "Urban Exploration", "Street Food",
                    "Public Transport Adventures", "Morning Rituals", "Art & Museums"
                ]
                chosen_theme = random.choice(themes) # Her seferinde birini rastgele seçiyoruz

                dynamic_user_prompt = (
                    f"Seed: {unique_id}. Topic category: {chosen_theme}. " # Temayı zorunlu kılıyoruz
                    f"Pick a SPECIFIC social activity under this category. "
                    f"Do not reuse common topics like 'coffee' or 'weather' unless it is essential."
                )

                response = await groq_client.quick_ask(
                    system_prompt=config["system_prompt"],
                    user_prompt=dynamic_user_prompt
                )
                
                # Başarılı ise kanala gönder (Groq tarafından yazısı silindi, kim tarafından istendiği yazıyor sadece.)
                say(
                    text=f"{config['title']} Card",
                    blocks=[
                        {
                            "type": "section",
                            "text": {"type": "mrkdwn", "text": response}
                        },
                        {
                            "type": "context",
                            "elements": [{
                                "type": "mrkdwn", 
                                "text": f"Requested by <@{user_id}>"}]
                        }
                    ]
                )
                
                # 5. COOLDOWN GÜNCELLEME (Başarıdan sonra listeye ekle)
                if user_id not in DAILY_COOLDOWN_STORAGE:
                    DAILY_COOLDOWN_STORAGE[user_id] = {}

                DAILY_COOLDOWN_STORAGE[user_id][user_text] = time.time()
                
            except Exception as e:
                logger.error(f"Daily error: {e}")
                respond(text="❌ Bir hata oluştu, lütfen daha sonra dene.")

        asyncio.run(process_daily())
        