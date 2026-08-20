from scripts.openai_compat_proxy import gemini_to_openai, openai_to_gemini


def test_openai_to_gemini_splits_system_and_user():
    body = openai_to_gemini(
        [
            {"role": "system", "content": "Return JSON."},
            {"role": "user", "content": "plan a story"},
        ]
    )
    assert body["systemInstruction"]["parts"][0]["text"] == "Return JSON."
    assert body["contents"][0]["role"] == "user"
    assert body["generationConfig"]["responseMimeType"] == "application/json"


def test_gemini_to_openai_extracts_text_and_tokens():
    out = gemini_to_openai(
        "reasoning-high",
        {
            "candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}],
            "usageMetadata": {"totalTokenCount": 9},
        },
    )
    assert out["choices"][0]["message"]["content"] == '{"ok": true}'
    assert out["usage"]["total_tokens"] == 9
    assert out["model"] == "reasoning-high"
