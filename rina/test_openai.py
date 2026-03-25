from openai import OpenAI

client = OpenAI(
    api_key="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"
    )

response = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Say hello"}],
)

print(response.choices[0].message.content)
