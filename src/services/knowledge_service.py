import os
import pandas as pd
from docx import Document
from typing import List, Dict, Any
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader
from src.core.logger import logger
from src.clients import VectorClient, GroqClient

class KnowledgeService:
    """
    Cemil'in 'Bilgi Küpü' (RAG). Dökümanları işler ve soruları yanıtlar.
    Tamamen ücretsiz ve limit-free yapıdadır.
    """

    def __init__(self, vector_client: VectorClient, groq_client: GroqClient):
        self.vector = vector_client
        self.groq = groq_client
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=1200,  # Daha büyük chunk'lar için artırıldı
            chunk_overlap=200  # Overlap de artırıldı
        )

    async def process_knowledge_base(self, folder_path: str = "knowledge_base"):
        """Belirtilen klasördeki dökümanları okur ve indekse ekler."""
        if not os.path.exists(folder_path):
            os.makedirs(folder_path)
            logger.warning(f"[!] {folder_path} bulunamadı, boş bir tane oluşturuldu.")
            return

        all_texts = []
        all_metadata = []

        for filename in os.listdir(folder_path):
            file_path = os.path.join(folder_path, filename)
            text = ""
            
            try:
                # PDF İşleme
                if filename.endswith(".pdf"):
                    reader = PdfReader(file_path)
                    for page in reader.pages:
                        text += page.extract_text() + "\n"
                
                # TXT ve Markdown İşleme
                elif filename.endswith((".txt", ".md")):
                    with open(file_path, "r", encoding="utf-8") as f:
                        text = f.read()

                # DOCX (Word) İşleme
                elif filename.endswith(".docx"):
                    doc = Document(file_path)
                    text = "\n".join([para.text for para in doc.paragraphs])

                # Excel ve CSV İşleme (Tablosal)
                elif filename.endswith((".csv", ".xlsx", ".xls")):
                    if filename.endswith(".csv"):
                        df = pd.read_csv(file_path)
                    else:
                        df = pd.read_excel(file_path)
                    
                    # Her satırı bir metin parçasına dönüştür
                    rows_text = []
                    for idx, row in df.iterrows():
                        row_str = ", ".join([f"{col}: {row[col]}" for col in df.columns])
                        rows_text.append(row_str)
                    text = "\n".join(rows_text)
                
                if text.strip():
                    chunks = self.splitter.split_text(text)
                    all_texts.extend(chunks)
                    all_metadata.extend([{"source": filename}] * len(chunks))
                    logger.info(f"[+] İşlendi: {filename} ({len(chunks)} parça)")

            except Exception as e:
                logger.error(f"[X] {filename} işlenirken hata: {e}")

        if all_texts:
            self.vector.add_texts(all_texts, all_metadata)
            logger.info(f"[!] {len(all_texts)} parça ile Bilgi Küpü güncellendi.")

    async def ask_question(self, question: str, user_id: str = "unknown") -> str:
        """Kullanıcının sorusunu dökümanlara göre yanıtlar."""
        try:
            logger.info(f"[>] Soru işleniyor | Kullanıcı: {user_id} | Soru: {question}")
            
            # 1. Benzer metin parçalarını bul (threshold ile filtrele)
            context_docs = self.model_search_context(question)
            
            if not context_docs:
                logger.warning(f"[!] Soru için dökümanlarda eşleşme bulunamadı | Soru: {question} | Kullanıcı: {user_id}")
                return (
                    "🤔 Üzgünüm, bilgi küpümde bu soruyla ilgili bir bilgi bulamadım.\n\n"
                    "Eğitim takvimi, kurallar veya genel bilgiler hakkında sorular sorabilirsin."
                )

            # 2. Bağlamı (Context) hazırla - Daha temiz format
            context_parts = []
            for i, doc in enumerate(context_docs, 1):
                source = doc['metadata'].get('source', 'Bilinmiyor')
                score = doc.get('score')
                # Score formatını düzelt
                if isinstance(score, float):
                    score_str = f"{score:.3f}"
                else:
                    score_str = "N/A"
                
                context_parts.append(
                    f"[Kaynak {i}: {source} | Benzerlik: {score_str}]\n"
                    f"{doc['text']}"
                )
            context_text = "\n\n---\n\n".join(context_parts)

            # -- GÜVENLİK KONTROLÜ (Prompt Injection Protection) --
            security_check = question.lower()
            forbidden_phrases = [
                "ignore previous instructions", "önceki talimatları yok say",
                "system prompt", "sistem talimatı",
                "you are now", "artık şusun",
                "act as", "gibi davran",
                "admin mode", "yönetici modu"
            ]
            if any(phrase in security_check for phrase in forbidden_phrases):
                logger.warning(f"[!] Prompt Injection Denemesi Engellendi: {user_id} - {question}")
                return "Üzgünüm, güvenlik protokollerim gereği bu tür talimatları işleyemiyorum. Sadece bilgi küpündeki verilerle yardımcı olabilirim. 🛡️"

            # 3. LLM'e (Groq) sor - Sıkı Kurallar Altında
            system_prompt = (
                "Sen Cemil'sin, Yapay Zeka Akademisi'nin yardımcı asistanısın. "
                "Sadece sana verilen BAĞLAM içindeki bilgileri kullanarak TÜRKÇE cevap veriyorsun.\n\n"
                "KESİN KURALLAR:\n"
                "1. SADECE verilen BAĞLAM'daki bilgileri kullan. Bağlamda yoksa 'Bu bilgi şu an elimde yok' de.\n"
                "2. YANITLARIN %100 TÜRKÇE OLMALI. Hiçbir İngilizce kelime, ifade veya terim kullanma.\n"
                "   - 'various' yerine 'çeşitli' de\n"
                "   - 'training' yerine 'eğitim' de\n"
                "   - 'course' yerine 'kurs' veya 'eğitim' de\n"
                "   - Tüm teknik terimleri Türkçeleştir\n"
                "3. Eğitim tarihleri, süreleri veya içerikler soruluyorsa, bağlamdaki TAM bilgiyi ver.\n"
                "4. Belirsiz cevaplar verme. Bilgi varsa net söyle, yoksa 'bilgim yok' de.\n"
                "5. Yanıtlarını maksimum 3-4 cümle ile sınırla, özlü ol.\n"
                "6. Kaynakları kendim ekleyeceğim, sen kaynak belirtme.\n\n"
                "DİL ZORUNLULUĞU: YANITINDA HİÇBİR İNGİLİZCE KELİME OLMAMALI. "
                "Eğer bağlamda İngilizce terim varsa, onu Türkçeye çevirerek kullan.\n"
                "ÖRNEK: 'various trainings' → 'çeşitli eğitimler', 'AI course' → 'yapay zeka eğitimi'\n"
            )
            
            user_prompt = (
                f"Aşağıdaki bağlamdaki bilgileri kullanarak soruyu TAMAMEN TÜRKÇE yanıtla. "
                f"Hiçbir İngilizce kelime kullanma:\n\n"
                f"BAĞLAM:\n{context_text}\n\n"
                f"SORU: {question}\n\n"
                f"CEVAP (SADECE TÜRKÇE, kısa ve net, hiçbir İngilizce kelime yok):"
            )
            
            answer = await self.groq.quick_ask(system_prompt, user_prompt)
            
            # 4. Kaynakları Ekle
            unique_sources = list(set([doc['metadata'].get('source', 'Bilinmiyor') for doc in context_docs]))
            if unique_sources:
                answer += f"\n\n[Kaynaklar: {', '.join(unique_sources)}]"
            
            return answer

        except Exception as e:
            logger.error(f"[X] KnowledgeService.ask_question hatası: {e}")
            return "Şu an hafızamı toparlamakta zorlanıyorum, birazdan tekrar sorar mısın? 🧠✨"

    def model_search_context(self, question: str) -> List[Dict]:
        """Vektör veritabanından bağlamı çeker."""
        # L2 mesafesi için: küçük mesafe = benzer, büyük mesafe = farklı
        # 0.8 ideal bir threshold - daha sıkı eşleşme için
        results = self.vector.search(question, top_k=8, threshold=0.8)
        
        if results:
            logger.info(f"[i] Vector search: {len(results)} eşleşme bulundu | Soru: {question[:50]}...")
            # İlk 3 sonucun skorlarını logla
            for i, res in enumerate(results[:3], 1):
                if res.get('score') is not None:
                    logger.info(f"[i] #{i} eşleşme skoru: {res['score']:.3f} | Kaynak: {res.get('metadata', {}).get('source', 'N/A')}")
        else:
            logger.warning(f"[!] Sıkı eşleşme bulunamadı (threshold: 0.8) | Soru: {question[:50]}...")
            # Threshold'u biraz gevşet ve tekrar dene
            results = self.vector.search(question, top_k=5, threshold=1.2)
            if results:
                logger.info(f"[i] Gevşek eşleşme ile {len(results)} sonuç bulundu (threshold: 1.2)")
                for i, res in enumerate(results[:2], 1):
                    if res.get('score') is not None:
                        logger.info(f"[i] #{i} eşleşme skoru: {res['score']:.3f}")
            else:
                logger.warning(f"[!] Hiçbir eşleşme bulunamadı (threshold: 1.2)")
        
        return results
