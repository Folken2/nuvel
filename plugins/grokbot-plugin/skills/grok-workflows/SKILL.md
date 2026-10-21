---
name: grok-workflows
description: Prompt templates and workflows for using xAI GrokBot with Nuvel — code, design, research, and automated pipelines.
---

# GrokBot Workflows for Nuvel

## Trigger Conditions
- Code generation with OrgMemory context
- Architecture research and design
- Automated code review in CI/CD pipelines
- DeepResearch tasks with web context
- Quick prototyping with image analysis for diagrams

## Prerequisites
- GrokBot configured for Nuvel (`grok-setup` skill)
- Grok API key (`XAI_API_KEY`) set for automated workflows
- Nuvel OrgMemory accessible for context gathering
- Helper scripts from `grok-setup` available

## Workflow Templates

### Workflow 1: OrgMemory-Grounded Code Generation

**When to use:** Implementing features with documented patterns

**Step 1: Prepare Context File**
```bash
cat > .grok/orgmemory-context.md << 'EOF'
## OrgMemory Context

### Architecture Decisions
[Paste relevant ADR from Nuvel]

### Coding Standards
[Paste relevant standards from Nuvel]

### Reference Implementation
[Describe or paste existing similar code pattern]
EOF
```

**Step 2: Use the API Client**
```python
from grok_client import ask_grok

prompt = """
## Implementation Request

Read the OrgMemory context from .grok/orgmemory-context.md for our standards.

### Task
Implement [feature description] following our documented patterns.

### Requirements
- Use the [pattern] from our ADRs
- Include proper error handling per our standards
- Write unit tests
- Add type hints throughout

### Deliverables
1. Implementation code
2. Unit tests
3. Notes for OrgMemory update
"""

result = ask_grok(system_prompt=NUVEL_SYSTEM_PROMPT, user_message=prompt)
print(result)
```

**Step 3: For Web Grok Users**

Paste the combined context + task prompt into the Grok web interface.

---

### Workflow 2: DeepResearch Architecture Analysis

**When to use:** Researching technology choices before recording ADRs

**Step 1: Define Research Scope**
```bash
cat > .grok/research-prompt.md << 'EOF'
## Architecture Research Task

### Question
What is the best approach for [technical decision] given our constraints?

### Current OrgMemory Context
**System Requirements:**
- Scale: [current/future load]
- Latency: [requirements]
- Team expertise: [languages/patterns team knows]

**Existing Decisions:**
- ADR-010: We use [current approach] because [reason]
- ADR-024: All new services must [constraint]

### Research Deliverables
1. Comparison of 2-3 approaches with trade-offs
2. Recommendation aligned with existing ADRs
3. New ADR draft for the chosen approach
4. Migration complexity estimate if replacing existing system
EOF
```

**Step 2: Run Research**
```python
# Use DeepSearch mode for web research
response = client.chat.completions.create(
    model="grok-3-deepsearch",
    messages=[{
        "role": "user",
        "content": open(".grok/research-prompt.md").read()
    }],
)
print(response.choices[0].message.content)
```

---

### Workflow 3: CI/CD Automated Code Review

**When to use:** Automated first-pass review in CI pipelines

**Step 1: Create Review Script**

Save as `scripts/grok-review.py`:
```python
#!/usr/bin/env python3
"""Automated code review using Grok with Nuvel standards."""
import os
import sys
import subprocess
from grok_client import ask_grok

def get_diff():
    """Get the git diff for the current PR."""
    base = os.environ.get("GITHUB_BASE_REF", "main")
    return subprocess.check_output(
        ["git", "diff", f"origin/{base}...HEAD"],
        text=True
    )

def get_orgmemory_standards():
    """Fetch relevant standards from OrgMemory context file."""
    standards_file = ".grok/orgmemory-standards.md"
    if os.path.exists(standards_file):
        with open(standards_file) as f:
            return f.read()
    return "No specific standards file found."

def main():
    diff = get_diff()
    standards = get_orgmemory_standards()
    
    if not diff.strip():
        print("No changes to review.")
        sys.exit(0)
    
    prompt = f"""
## Automated Code Review

### OrgMemory Standards
{standards}

### Changes to Review
```diff
{diff}
```

### Review Instructions
Check the diff against our OrgMemory standards. Focus on:
1. Standards violations
2. Security issues
3. Obvious bugs
4. Missing error handling

### Output Format
STATUS: [PASS|WARN|FAIL]
FINDINGS:
- [severity] [file:line] [description]
SUGGESTIONS:
- [optional improvement]
"""
    
    review = ask_grok(NUVEL_SYSTEM_PROMPT, prompt)
    
    # Parse status for CI exit code
    if "STATUS: FAIL" in review:
        print(review)
        sys.exit(1)
    elif "STATUS: WARN" in review:
        print(review)
        sys.exit(0)  # Warning doesn't block
    else:
        print(review)
        sys.exit(0)

if __name__ == "__main__":
    main()
```

**Step 2: Add to CI Config**

GitHub Actions example:
```yaml
# .github/workflows/grok-review.yml
name: Grok Code Review
on: [pull_request]

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install openai
      - name: Run Grok Review
        env:
          XAI_API_KEY: ${{ secrets.XAI_API_KEY }}
        run: python scripts/grok-review.py
```

---

### Workflow 4: Image-Based Architecture Review

**When to use:** Reviewing architecture diagrams, ERDs, or flowcharts

**Step 1: Export Diagram**

Export your architecture diagram as PNG from your diagramming tool.

**Step 2: Review with Grok**

```python
import base64

with open("architecture.png", "rb") as f:
    image_b64 = base64.b64encode(f.read()).decode()

response = client.chat.completions.create(
    model="grok-3",
    messages=[{
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": """Review this architecture diagram against our standards:

### OrgMemory ADRs
- ADR-012: Services communicate via async messaging, not direct HTTP
- ADR-018: All databases are accessed through a data service layer
- ADR-023: Authentication is centralized via Auth Gateway

### Review Questions
1. Does this diagram violate any of our ADRs?
2. Are there missing components based on our standards?
3. What improvements would you suggest?
4. Flag any single points of failure.

Output as structured review with specific ADR references."""
            },
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{image_b64}"}
            }
        ]
    }],
)
print(response.choices[0].message.content)
```

---

## Quick Prompt Templates

### Bug Investigation
```
Bug: [description]. 
Check against past incidents in OrgMemory: [paste relevant entries].
Component docs: [paste from OrgMemory].
Find root cause and fix with test.
```

### API Design
```
Design REST API for [resource].
Follow API conventions from OrgMemory: [paste standards].
Include: endpoints, request/response schemas, error codes, auth requirements.
```

### Test Generation
```
Generate tests for [function/component].
Follow testing patterns from OrgMemory: [paste conventions].
Cover: happy path, edge cases, error conditions, boundary values.
```

## Pitfalls
- **DeepSearch cost**: DeepSearch mode may consume more tokens. Use only for research tasks that genuinely need web access.
- **Image size limits**: Grok has limits on image dimensions and file sizes for analysis. Resize large diagrams before uploading.
- **CI rate limits**: Automated reviews in CI may hit API rate limits on busy repos. Implement caching or batching for high-PR-volume projects.
- **Context freshness**: Grok's knowledge cutoff may not include recent framework updates. Supplement with web search or OrgMemory context for recent changes.

## Verification
1. Generated code compiles and passes tests
2. DeepSearch results are current and relevant
3. CI review pipeline runs without errors
4. Image-based reviews catch ADR violations
5. All outputs flag OrgMemory update needs where applicable