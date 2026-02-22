# app/services/ai_orchestrator.py
import os
import logging
from groq import Groq
import requests
import google.generativeai as genai

logger = logging.getLogger(__name__)

class GroqClient:
    def __init__(self):
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY not set")
        self.client = Groq(api_key=api_key)
        self.model = "llama3-70b-8192"

    def generate(self, messages, temperature=0.7, max_tokens=1000):
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens
        )
        return response.choices[0].message.content

class GitHubModelsClient:
    def __init__(self):
        self.token = os.getenv("GITHUB_TOKEN")
        if not self.token:
            raise ValueError("GITHUB_TOKEN not set")
        self.endpoint = "https://models.inference.ai.azure.com/chat/completions"
        self.model = "gpt-4o-mini"

    def generate(self, messages, temperature=0.7, max_tokens=1000):
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        response = requests.post(self.endpoint, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

class GeminiClient:
    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY not set")
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel("gemini-1.5-flash")

    def generate(self, messages, temperature=0.7, max_tokens=1000):
        # Convert messages to Gemini format
        prompt = ""
        for msg in messages:
            role = msg["role"]
            content = msg["content"]
            if role == "system":
                prompt += f"System: {content}\n"
            elif role == "user":
                prompt += f"User: {content}\n"
            elif role == "assistant":
                prompt += f"Assistant: {content}\n"
        response = self.model.generate_content(
            prompt,
            generation_config={
                "temperature": temperature,
                "max_output_tokens": max_tokens
            }
        )
        return response.text

class AIOrchestrator:
    def __init__(self):
        self.providers = {
            "groq": GroqClient(),
            "github": GitHubModelsClient(),
            "gemini": GeminiClient()
        }
        self.preferred_order = ["groq", "github", "gemini"]

    def generate_insights(self, system_prompt, user_prompt, use_deep_history=False):
        """
        Generate insights using multi-model failover.
        If use_deep_history=True, start with Gemini.
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        if use_deep_history:
            provider_sequence = ["gemini"] + [p for p in self.preferred_order if p != "gemini"]
        else:
            provider_sequence = self.preferred_order

        for provider_name in provider_sequence:
            try:
                logger.info(f"Trying provider: {provider_name}")
                provider = self.providers[provider_name]
                result = provider.generate(messages)
                return result
            except Exception as e:
                logger.warning(f"{provider_name} failed: {e}")
                continue

        raise Exception("All AI providers failed")
