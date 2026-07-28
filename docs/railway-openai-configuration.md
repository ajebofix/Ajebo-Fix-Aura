# Aura OpenAI configuration on Railway

A.J. Rina reads the official OpenAI environment variable:

```text
OPENAI_API_KEY=<OpenAI API key>
```

The variable must be configured on the **Ajebo-Fix-Aura web service** in the
Railway production environment.

An earlier Aura environment used this non-standard spelling:

```text
OPEN_AI_KEY
```

Aura temporarily recognises that spelling as a compatibility alias, but it should
be replaced with `OPENAI_API_KEY`. Keep only the canonical variable after the new
deployment has been verified.

## Deployment check

1. Add `OPENAI_API_KEY` to the Aura web service.
2. Remove `OPEN_AI_KEY` after the replacement is saved.
3. Wait for Railway to deploy and show **Active**.
4. Open `/version` and confirm the deployed commit is current.
5. Send one simple message to A.J. Rina.

The production log must no longer contain:

```text
AI ERROR: OPENAI_API_KEY is not set
```

Never place an API key in GitHub, source code, screenshots, templates or client
responses.
