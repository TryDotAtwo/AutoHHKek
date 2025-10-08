import asyncio
import json
import random
from typing import List, Dict, Any, Optional, Tuple
import os

import requests
import g4f
from g4f.errors import ModelNotFoundError

_semaphore = asyncio.Semaphore(10)


def download_working_results(url: str, filename: str) -> bool:
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        with open(filename, "w", encoding="utf-8") as f:
            f.write(r.text)
        return True
    except requests.RequestException as e:
        print(f"Download error: {e}")
        return False


def get_working_combinations(filepath: str) -> List[Tuple[str, str, str]]:
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        lines = content.strip().split('\n')
        combinations = []
        for line in lines:
            if '|' in line:
                parts = line.strip().split('|')
                if len(parts) == 3:
                    provider, model, typ = parts
                    combinations.append((provider, model, typ))
        combinations = list(set(combinations))  # Удаляем дубли
        return combinations
    except Exception as e:
        print(f"Error reading file: {e}. Using fallback.")
        return []


def load_successful_combos(filename: str) -> set:
    """Загружает успешные комбинации из файла."""
    if os.path.exists(filename):
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return set(tuple(c) for c in data)
        except Exception as e:
            print(f"Error loading successful combos: {e}")
    return set()


def save_successful_combos(filename: str, combos: set):
    """Сохраняет успешные комбинации в файл."""
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump([list(c) for c in combos], f, ensure_ascii=False)
    except Exception as e:
        print(f"Error saving successful combos: {e}")


async def try_combo(provider_name: str, model_name: str, typ: str, system_prompt: str, user_prompt: str) -> Optional[Dict[str, Any]]:
    """Одна попытка запроса к LLM с конкретной комбинацией."""
    try:
        async with _semaphore:
            await asyncio.sleep(random.uniform(0.1, 0.6))  # имитация человека
            provider_class = getattr(g4f.Provider, provider_name, None)
            if not provider_class:
                return None

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            resp = await asyncio.wait_for(
                g4f.ChatCompletion.create_async(model=model_name, messages=messages, provider=provider_class),
                timeout=10
            )

        if isinstance(resp, str) and resp.strip():
            start = resp.find("{")
            end = resp.rfind("}")
            if start != -1 and end != -1 and end > start:
                candidate = resp[start:end+1]
                try:
                    payload = json.loads(candidate)
                    if isinstance(payload, dict):
                        return payload
                except json.JSONDecodeError:
                    pass

    except ModelNotFoundError:
        pass
    except asyncio.TimeoutError:
        print(f"Таймаут для {model_name}/{provider_name}")
    except Exception as e:
        print(f"Ошибка для {model_name}/{provider_name}: {e}")
    return None


async def robust_llm_query(system_prompt: str, user_prompt: str) -> Optional[Dict[str, Any]]:
    """Подбирает работающую модель/провайдера: приоритетные → успешные → остальные."""
    priority_combos = [
        ("CohereForAI_C4AI_Command", "command-a-03-2025", "text"),
        ("CohereForAI_C4AI_Command", "command-r-plus-08-2024", "text"),
        ("CohereForAI_C4AI_Command", "command-r-08-2024", "text"),
        ("CohereForAI_C4AI_Command", "command-r7b-12-2024", "text"),
    ]

    # working_results.txt
    local_filename = "working_results.txt"
    if not os.path.exists(local_filename):
        url = "https://raw.githubusercontent.com/maruf009sultan/g4f-working/refs/heads/main/working/working_results.txt"
        download_working_results(url, local_filename)
    all_combos = get_working_combinations(local_filename)

    # Загружаем успешные
    successful_filename = "successful_combos.json"
    successful_combos = load_successful_combos(successful_filename)

    # === 1. Приоритетные комбинации (с 3 ретраями) ===
    for provider_name, model_name, typ in priority_combos:
        for attempt in range(3):
            payload = await try_combo(provider_name, model_name, typ, system_prompt, user_prompt)
            if payload:
                combo = (provider_name, model_name, typ)
                if combo not in successful_combos:
                    successful_combos.add(combo)
                    save_successful_combos(successful_filename, successful_combos)
                return payload
            await asyncio.sleep(0.5)  # небольшая пауза между ретраями

    # === 2. Успешные комбинации ===
    for provider_name, model_name, typ in successful_combos:
        payload = await try_combo(provider_name, model_name, typ, system_prompt, user_prompt)
        if payload:
            return payload
        await asyncio.sleep(0.5)

    # === 3. Остальные (рандомизированные) ===
    random.shuffle(all_combos)
    for provider_name, model_name, typ in all_combos[:20]:  # ограничиваем 20
        payload = await try_combo(provider_name, model_name, typ, system_prompt, user_prompt)
        if payload:
            combo = (provider_name, model_name, typ)
            if combo not in successful_combos:
                successful_combos.add(combo)
                save_successful_combos(successful_filename, successful_combos)
            return payload
        await asyncio.sleep(1.0)

    print("Не удалось найти работающую модель/провайдера.")
    return None
