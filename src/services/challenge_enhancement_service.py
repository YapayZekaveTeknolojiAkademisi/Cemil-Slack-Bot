"""
Challenge projelerini LLM ile özelleştiren servis.
"""

import json
from typing import Dict, List, Any
from src.core.logger import logger
from src.clients import GroqClient
from src.services import KnowledgeService


class ChallengeEnhancementService:
    """
    Challenge projelerini LLM ile özelleştiren servis.
    """

    def __init__(self, groq_client: GroqClient, knowledge_service: KnowledgeService):
        self.groq = groq_client
        self.knowledge = knowledge_service

    async def enhance_project(
        self,
        base_project: Dict,
        team_size: int,
        deadline_hours: int,
        theme: str
    ) -> Dict:
        """
        Temel projeye LLM ile özellikler ekler.
        """
        try:
            # 1. Knowledge base'den ilgili bilgileri al
            relevant_knowledge = await self._get_relevant_knowledge(
                theme=theme,
                project_name=base_project.get("name", "")
            )

            # 2. LLM prompt hazırla
            system_prompt = f"""
            Sen bir yazılım proje danışmanısın. Verilen projeye 
            {team_size} kişilik bir takımın {deadline_hours} saatte 
            ekleyebileceği 2-3 anlamlı ve yaratıcı özellik öner.
            
            Özellikler:
            - Gerçekçi olmalı (zaman sınırına uygun)
            - Projeye değer katmalı
            - Öğrenme fırsatı sunmalı
            - Takım çalışması gerektirmeli
            - Teknik olarak uygulanabilir olmalı
            
            Çıktı formatı (JSON):
            {{
                "features": [
                    {{
                        "name": "Özellik Adı",
                        "description": "Kısa açıklama",
                        "estimated_hours": 8,
                        "difficulty": "intermediate",
                        "learning_value": "Ne öğrenilir",
                        "tasks": ["Görev 1", "Görev 2"]
                    }}
                ]
            }}
            
            Sadece JSON döndür, başka açıklama yapma.
            """

            user_prompt = f"""
            Tema: {theme}
            Proje: {base_project.get('name', '')}
            Açıklama: {base_project.get('description', '')}
            
            Mevcut Görevler:
            {self._format_tasks(base_project.get('tasks', []))}
            
            İlgili Bilgiler:
            {relevant_knowledge}
            
            Bu projeye eklenebilecek 2-3 özellik öner.
            """

            # 3. LLM'den yanıt al
            llm_response = await self.groq.quick_ask(system_prompt, user_prompt)

            # 4. JSON parse et
            enhanced_features = self._parse_llm_response(llm_response)

            # 5. Yeni görevler oluştur
            new_tasks = self._create_tasks_from_features(enhanced_features)

            # 6. Projeyi güncelle
            enhanced_project = {
                **base_project,
                "llm_enhanced_features": enhanced_features,
                "tasks": base_project.get("tasks", []) + new_tasks,
            }

            logger.info(f"[+] LLM özelleştirmesi tamamlandı: {len(enhanced_features)} özellik eklendi")
            return enhanced_project

        except Exception as e:
            logger.error(f"[X] ChallengeEnhancementService.enhance_project hatası: {e}", exc_info=True)
            # Hata durumunda orijinal projeyi döndür
            return base_project

    async def _get_relevant_knowledge(
        self,
        theme: str,
        project_name: str
    ) -> str:
        """
        Knowledge base'den ilgili bilgileri getir.
        """
        try:
            query = f"{theme} {project_name} best practices guidelines"
            results = await self.knowledge.model_search_context(query, top_k=3)

            if not results:
                return "İlgili bilgi bulunamadı."

            knowledge_text = "\n".join([
                f"- {r.get('source', 'Unknown')}: {r.get('text', '')[:200]}..."
                for r in results
            ])

            return knowledge_text
        except Exception as e:
            logger.warning(f"[!] Knowledge base sorgusu hatası: {e}")
            return ""

    def _format_tasks(self, tasks: List[Dict]) -> str:
        """Görevleri formatla."""
        if not tasks:
            return "Görev yok"
        
        # Eğer tasks JSON string ise parse et
        if isinstance(tasks, str):
            try:
                tasks = json.loads(tasks)
            except:
                return "Görev formatı hatalı"
        
        return "\n".join([
            f"- {task.get('title', task.get('name', 'Unknown'))}: {task.get('description', '')}"
            for task in tasks
        ])

    def _parse_llm_response(self, response: str) -> List[Dict]:
        """LLM yanıtını parse et."""
        import json
        try:
            # JSON extract et
            if "```json" in response:
                json_str = response.split("```json")[1].split("```")[0].strip()
            elif "```" in response:
                json_str = response.split("```")[1].split("```")[0].strip()
            else:
                json_str = response.strip()

            data = json.loads(json_str)
            return data.get("features", [])
        except Exception as e:
            logger.error(f"[X] LLM response parse hatası: {e}")
            logger.debug(f"[i] LLM Response: {response[:500]}")
            return []

    def _create_tasks_from_features(
        self,
        features: List[Dict]
    ) -> List[Dict]:
        """LLM özelliklerinden görevler oluştur."""
        new_tasks = []

        for i, feature in enumerate(features):
            task = {
                "id": f"task_llm_{i + 1}",
                "title": feature.get("name", "LLM Özellik"),
                "description": feature.get("description", ""),
                "deliverable": f"{feature.get('name', 'Özellik')} implementasyonu",
                "estimated_hours": feature.get("estimated_hours", 8),
                "difficulty": feature.get("difficulty", "intermediate"),
                "skills": ["LLM Enhanced Feature"],
                "checklist": feature.get("tasks", []),
                "resources": [],
                "is_llm_enhanced": True
            }
            new_tasks.append(task)

        return new_tasks
