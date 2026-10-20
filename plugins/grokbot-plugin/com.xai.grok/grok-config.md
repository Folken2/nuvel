# Grok Configuration for Nuvel

## API Configuration

### Environment Variables
```bash
export XAI_API_KEY="xai-..."
export NUVEL_API_KEY="nv-..."
export NUVEL_ORG_ID="org_..."
```

### Python Client
```python
import os
from openai import OpenAI

# Grok uses OpenAI-compatible API at x.ai
client = OpenAI(
    api_key=os.environ["XAI_API_KEY"],
    base_url="https://api.x.ai/v1",
)

NUVEL_SYSTEM_PROMPT = """You are a software engineering assistant integrated with
Nuvel (nuvel.dev) team knowledge management via OrgMemory.

When provided with OrgMemory context (architecture decisions, coding standards,
component docs), ground your responses in those existing patterns.

Output Structure:
1. Context Check: note alignment with provided OrgMemory standards
2. Solution: implementation with code
3. OrgMemory Actions: what should be recorded after implementation
4. Trade-offs: alternatives considered

Always flag when your suggestion would deviate from established OrgMemory patterns."""

def ask_grok_with_nuvel(user_message: str, orgmemory_context: str = "") -> str:
    """Send a prompt to Grok with Nuvel OrgMemory context."""
    system = NUVEL_SYSTEM_PROMPT
    if orgmemory_context:
        system += f"\n\n## Current OrgMemory Context\n{orgmemory_context}"
    
    response = client.chat.completions.create(
        model="grok-3",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ],
        temperature=0.3,
    )
    return response.choices[0].message.content
```

### Node.js Client
```javascript
import OpenAI from 'openai';

const client = new OpenAI({
  apiKey: process.env.XAI_API_KEY,
  baseURL: 'https://api.x.ai/v1',
});

const NUVEL_SYSTEM_PROMPT = `You are a software engineering assistant integrated with
Nuvel (nuvel.dev) team knowledge management via OrgMemory.

When provided with OrgMemory context (architecture decisions, coding standards,
component docs), ground your responses in those existing patterns.

Output Structure:
1. Context Check: note alignment with provided OrgMemory standards
2. Solution: implementation with code
3. OrgMemory Actions: what should be recorded after implementation
4. Trade-offs: alternatives considered

Always flag when your suggestion would deviate from established OrgMemory patterns.`;

export async function askGrok(message, orgmemoryContext = '') {
  let system = NUVEL_SYSTEM_PROMPT;
  if (orgmemoryContext) {
    system += `\n\n## Current OrgMemory Context\n${orgmemoryContext}`;
  }
  
  const response = await client.chat.completions.create({
    model: 'grok-3',
    messages: [
      { role: 'system', content: system },
      { role: 'user', content: message },
    ],
    temperature: 0.3,
  });
  return response.choices[0].message.content;
}
```

## Grok Models

| Model | Use Case | Context Window |
|-------|----------|---------------|
| `grok-3` | General coding, analysis | 131K tokens |
| `grok-3-deepsearch` | Research, architecture analysis | 131K tokens |
| `grok-3-fast` | Quick responses, prototyping | 131K tokens |

## Rate Limits

Check your plan at https://console.x.ai for current rate limits.
Implement exponential backoff for automated workflows:
```python
import time
from openai import RateLimitError

def ask_grok_with_retry(*args, max_retries=3, **kwargs):
    for attempt in range(max_retries):
        try:
            return ask_grok_with_nuvel(*args, **kwargs)
        except RateLimitError:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            else:
                raise
```

## Web Interface Quick Setup

For the Grok web interface (grok.com), paste this at session start:
```
Nuvel Context: I use Nuvel (nuvel.dev) OrgMemory for team architecture decisions
and coding standards. I'll share relevant context. Ground answers in it.
```