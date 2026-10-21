---
name: grok-setup
description: Configure xAI GrokBot with Nuvel API integration and project context for Nuvel-aligned development.
---

# GrokBot Setup for Nuvel

## Trigger Conditions
- Setting up GrokBot for Nuvel-integrated development
- Configuring Grok API access for automated workflows
- Onboarding team members to use Grok with Nuvel context
- Integrating Grok into a Nuvel pipeline (CI/CD, code review)

## Prerequisites
- xAI Grok account (grok.com or X Premium+)
- Grok API key (available at console.x.ai for API access)
- A Nuvel account with OrgMemory access (https://nuvel.dev)
- For API workflows: Python 3.10+ or Node.js 18+

## Steps

### 1. Access Grok

**Option A: Web Interface (grok.com)**
1. Go to https://grok.com
2. Sign in with your X account or xAI account
3. Verify subscription tier (free tier available, Pro for extended usage)

**Option B: API Access**
1. Go to https://console.x.ai
2. Generate an API key
3. Set the key:
```bash
export XAI_API_KEY="xai-..."
echo 'export XAI_API_KEY="xai-..."' >> ~/.bashrc
```

### 2. Configure Nuvel Context

**For Web Interface:**

Create a context template to paste at session start:

```markdown
## Nuvel Integration Context
I work at [Company] on [Project]. We use Nuvel (nuvel.dev) for team knowledge
management via OrgMemory — a Semantica-based knowledge graph containing:
- Architecture Decision Records (ADRs)
- Coding standards and conventions
- Component documentation and API specs
- Past incident reports

When I provide OrgMemory context, ground your answers in those existing
decisions and standards. Flag when a suggestion would deviate from
established patterns.

**Tech Stack:** [languages/frameworks]
**Team Conventions:** [brief list]
**OrgMemory Reference:** https://nuvel.dev/org/[org-id]
```

**For API / Automated Workflows:**

Create a system prompt template:

```python
# grok_nuvel_context.py
NUVEL_SYSTEM_PROMPT = """You are a software engineering assistant integrated with 
Nuvel (nuvel.dev) team knowledge management.

When provided with OrgMemory context (architecture decisions, coding standards, 
component docs), ground your responses in those existing patterns.

Output Structure:
1. Context Check: note alignment with provided OrgMemory standards
2. Solution: implementation with code
3. OrgMemory Actions: what should be recorded after implementation
4. Trade-offs: alternatives considered

Always flag when your suggestion would deviate from established OrgMemory patterns."""
```

### 3. Set Up API Client

**Python:**
```python
# grok_client.py
import os
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["XAI_API_KEY"],
    base_url="https://api.x.ai/v1",
)

def ask_grok(system_prompt: str, user_message: str, model: str = "grok-3"):
    """Send a prompt to Grok with Nuvel context."""
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        temperature=0.3,
    )
    return response.choices[0].message.content
```

**Node.js:**
```javascript
// grok_client.js
import OpenAI from 'openai';

const client = new OpenAI({
  apiKey: process.env.XAI_API_KEY,
  baseURL: 'https://api.x.ai/v1',
});

export async function askGrok(systemPrompt, userMessage, model = 'grok-3') {
  const response = await client.chat.completions.create({
    model,
    messages: [
      { role: 'system', content: systemPrompt },
      { role: 'user', content: userMessage },
    ],
    temperature: 0.3,
  });
  return response.choices[0].message.content;
}
```

### 4. Create Nuvel Workflow Helper

Save as `grok_nuvel.py`:
```python
#!/usr/bin/env python3
"""Grok + Nuvel integration helper."""
import os
import sys
from grok_client import ask_grok
from grok_nuvel_context import NUVEL_SYSTEM_PROMPT

def main():
    task = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else input("Task: ")
    
    # Optionally read OrgMemory context from file
    context_file = ".grok/orgmemory-context.md"
    context = ""
    if os.path.exists(context_file):
        with open(context_file) as f:
            context = f.read()
    
    prompt = f"{context}\n\n## Task\n{task}" if context else task
    result = ask_grok(NUVEL_SYSTEM_PROMPT, prompt)
    print(result)

if __name__ == "__main__":
    main()
```

Make executable:
```bash
chmod +x grok_nuvel.py
```

### 5. Verify Configuration

```bash
# Test API access
python3 -c "
from grok_client import ask_grok
result = ask_grok('Be concise.', 'Say: Grok + Nuvel integration verified.')
print(result)
"

# Test with OrgMemory context
echo '## OrgMemory Context
ADR-001: All APIs use REST with JSON.
Coding Standard: Python with type hints required.' > .grok/orgmemory-context.md

python3 grok_nuvel.py "How should I structure a new API endpoint?"
```

## Grok-Specific Capabilities

### DeepSearch
Grok's DeepSearch feature can research across the web. Use it for:
```python
# DeepSearch mode for research tasks
response = client.chat.completions.create(
    model="grok-3-deepsearch",
    messages=[{"role": "user", "content": "Research best practices for [topic]"}],
)
```

### Image Understanding
Grok can analyze images. Use for architecture diagram review:
```python
response = client.chat.completions.create(
    model="grok-3",
    messages=[
        {"role": "user", "content": [
            {"type": "text", "text": "Review this architecture diagram against our ADRs."},
            {"type": "image_url", "image_url": {"url": "https://example.com/diagram.png"}},
        ]},
    ],
)
```

## Pitfalls
- **API compatibility**: Grok's API is OpenAI-compatible but may have subtle differences. Test thoroughly before building automation on it.
- **No MCP support**: Grok does not support MCP servers. For direct OrgMemory access, use Codex CLI or Claude Code CLI.
- **Rate limits**: Grok API has rate limits. For heavy automated usage, implement exponential backoff.
- **DeepSearch availability**: DeepSearch mode may not be available on all API tiers. Check console.x.ai for your plan's capabilities.
- **X account requirement**: Web Grok typically requires an X account. For team use without individual X accounts, use the API.

## Verification
1. `XAI_API_KEY` environment variable is set
2. API test call returns a valid response
3. Grok references provided OrgMemory context in responses
4. Helper script `grok_nuvel.py` executes successfully
5. Responses follow the Nuvel output structure