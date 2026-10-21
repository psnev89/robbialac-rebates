"""Extrai trabalhos de pintura de um Caderno de Encargos (PDF) com um LLM."""

import base64
import json
from io import BytesIO

import pandas as pd
from pydantic import BaseModel, Field

PROVIDERS = {
    "Google Gemini": {
        "secret": "GEMINI_API_KEY",
        "models": ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro"],
    },
    "Anthropic Claude": {
        "secret": "ANTHROPIC_API_KEY",
        "models": ["claude-sonnet-4-5", "claude-haiku-4-5", "claude-opus-4-1"],
    },
    "OpenAI": {
        "secret": "OPENAI_API_KEY",
        "models": ["gpt-5-mini", "gpt-5", "gpt-4o"],
    },
}

JOB_COLUMNS = ["capitulo", "artigo", "descricao", "unidade", "quantidade"]

JOB_LABELS = {
    "capitulo": "Capítulo",
    "artigo": "Artigo",
    "descricao": "Descrição",
    "unidade": "Unidade",
    "quantidade": "Quantidade",
}


class PaintJob(BaseModel):
    capitulo: str = Field(default="", description="Capítulo/secção do caderno, ex.: '7. Pinturas'")
    artigo: str = Field(default="", description="Número do artigo/item, ex.: '7.3'; vazio se não existir")
    descricao: str = Field(description="Texto completo da descrição do trabalho")
    unidade: str = Field(default="", description="Unidade de medição: m2, m, un, vg, kg, l, …")
    quantidade: float | None = Field(default=None, description="Quantidade numérica ou null se não indicada")


PROMPT = """\
Analisa os documentos anexos (Caderno de Encargos e, quando incluídos, Mapa \
de Quantidades ou Medições) e extrai TODOS os trabalhos de pintura: \
pintura de paredes, tetos, fachadas, carpintarias, serralharias, barramentos, \
primários e preparação de superfícies diretamente associada à pintura. \
Cruza a informação entre documentos quando necessário.

Regras:
- Inclui apenas trabalhos de pintura. Não incluas rebocos, estuques, \
revestimentos cerâmicos, pavimentos, impermeabilizações nem outros trabalhos.
- Mantém a descrição completa do documento, incluindo tipo de tinta, número \
de demãos, marca/sistema especificado e superfície a pintar.
- As quantidades são críticas: extrai sempre a área em m2 (e outras unidades) \
quando o documento as indica, incluindo em mapas de quantidades ou medições \
anexos. Se a quantidade não estiver indicada no documento, usa null.
- Se um artigo abranger várias sub-alíneas de pintura, extrai cada uma \
separadamente.
- Se o documento não contiver trabalhos de pintura, devolve uma lista vazia.
"""

JSON_INSTRUCTION = (
    "Responde APENAS com um array JSON de objetos com as chaves "
    "capitulo, artigo, descricao, unidade, quantidade. Sem markdown nem texto adicional."
)


def parse_jobs(payload) -> pd.DataFrame:
    """Convert the model payload (dicts or models) into a DataFrame."""
    rows = []
    for item in payload or []:
        getter = item.get if isinstance(item, dict) else lambda k, d=None: getattr(item, k, d)
        rows.append({column: getter(column, None) for column in JOB_COLUMNS})
    return pd.DataFrame(rows, columns=JOB_COLUMNS)


def _loads_json(text: str):
    """Parse a JSON array, tolerating markdown fences in the reply."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text)


def _extract_gemini(docs: list[tuple[str, bytes]], api_key: str, model: str) -> pd.DataFrame:
    from google import genai

    client = genai.Client(api_key=api_key)
    uploaded = []
    try:
        for name, data in docs:
            uploaded.append(
                client.files.upload(
                    file=BytesIO(data),
                    config={"mime_type": "application/pdf", "display_name": name},
                )
            )
    except Exception as e:
        raise ValueError(f"Não foi possível enviar o(s) PDF(s): {e}") from e
    try:
        response = client.models.generate_content(
            model=model,
            contents=[*uploaded, PROMPT],
            config={
                "response_mime_type": "application/json",
                "response_schema": list[PaintJob],
            },
        )
    except Exception as e:
        raise ValueError(f"Falha na extração com o Gemini: {e}") from e
    finally:
        for doc in uploaded:
            try:
                client.files.delete(name=doc.name)
            except Exception:
                pass
    return parse_jobs(response.parsed)


def _extract_claude(docs: list[tuple[str, bytes]], api_key: str, model: str) -> pd.DataFrame:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    try:
        message = client.messages.create(
            model=model,
            max_tokens=16384,
            messages=[
                {
                    "role": "user",
                    "content": [
                        *[
                            {
                                "type": "document",
                                "source": {
                                    "type": "base64",
                                    "media_type": "application/pdf",
                                    "data": base64.standard_b64encode(data).decode(),
                                },
                            }
                            for _, data in docs
                        ],
                        {"type": "text", "text": PROMPT + "\n\n" + JSON_INSTRUCTION},
                    ],
                }
            ],
        )
        payload = _loads_json(message.content[0].text)
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"Falha na extração com o Claude: {e}") from e
    return parse_jobs(payload)


def _extract_openai(docs: list[tuple[str, bytes]], api_key: str, model: str) -> pd.DataFrame:
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    job_schema = {
        "type": "object",
        "properties": {
            "capitulo": {"type": "string"},
            "artigo": {"type": "string"},
            "descricao": {"type": "string"},
            "unidade": {"type": "string"},
            "quantidade": {"type": ["number", "null"]},
        },
        "required": ["capitulo", "artigo", "descricao", "unidade", "quantidade"],
        "additionalProperties": False,
    }
    try:
        response = client.responses.create(
            model=model,
            input=[
                {
                    "role": "user",
                    "content": [
                        *[
                            {
                                "type": "input_file",
                                "filename": name,
                                "file_data": (
                                    "data:application/pdf;base64,"
                                    + base64.standard_b64encode(data).decode()
                                ),
                            }
                            for name, data in docs
                        ],
                        {"type": "input_text", "text": PROMPT},
                    ],
                }
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "paint_jobs",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {"jobs": {"type": "array", "items": job_schema}},
                        "required": ["jobs"],
                        "additionalProperties": False,
                    },
                }
            },
        )
        payload = json.loads(response.output_text)["jobs"]
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"Falha na extração com a OpenAI: {e}") from e
    return parse_jobs(payload)


_EXTRACTORS = {
    "Google Gemini": _extract_gemini,
    "Anthropic Claude": _extract_claude,
    "OpenAI": _extract_openai,
}


def extract_paint_jobs(
    docs: list[tuple[str, bytes]], api_key: str, provider: str, model: str
) -> pd.DataFrame:
    """Send the PDFs (name, bytes) to the chosen provider; return the paint jobs."""
    return _EXTRACTORS[provider](docs, api_key, model)
