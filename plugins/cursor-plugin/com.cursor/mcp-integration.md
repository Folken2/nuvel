# MCP Integration in Cursor

## Adding Nuvel OrgMemory to Cursor

### Method 1: Cursor Settings UI

1. Open Cursor Settings (`Cmd+,` or `Ctrl+,`)
2. Navigate to **MCP** section
3. Click **Add New MCP Server**
4. Fill in:
   - **Name:** Nuvel OrgMemory
   - **Type:** stdio
   - **Command:** npx
   - **Args:** -y @nuvel/mcp-server-orgmemory
   - **Environment Variables:**
     - NUVEL_API_KEY=your-key
     - NUVEL_ORG_ID=your-org-id

### Method 2: mcp.json File

Create or edit `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "nuvel-orgmemory": {
      "command": "npx",
      "args": ["-y", "@nuvel/mcp-server-orgmemory"],
      "env": {
        "NUVEL_API_KEY": "nv-your-api-key",
        "NUVEL_ORG_ID": "org-your-org-id"
      }
    }
  }
}
```

Restart Cursor after adding the MCP server.

### Method 3: Using the Plugin's mcp.json

Copy the plugin's `mcp.json` to your Cursor config:
```bash
cp mcp.json ~/.cursor/mcp.json
# Edit env values with your actual keys
```

## Verifying MCP Connection

In Cursor Chat (`Cmd+L`):
```
List all available MCP tools. Is nuvel-orgmemory connected?
```

Expected: nuvel-orgmemory tools appear in the list.

## Using OrgMemory in Cursor

### In Chat (Cmd+L)
```
Search nuvel-orgmemory for architecture decisions about [topic]
```

### In Agent Mode (Cmd+I)
```
Implement [feature]. First, check nuvel-orgmemory for relevant patterns and standards.
```

### In Inline Edit (Cmd+K)
```
Refactor this following our standards from OrgMemory: [paste or reference]
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| MCP server not starting | Install globally: `npm install -g @nuvel/mcp-server-orgmemory` |
| Env vars not picked up | Use full path in command or set vars in mcp.json env field |
| npx not found | Ensure Node.js 18+ is installed: `node --version` |
| Package not resolving | Use full path: `/usr/local/bin/npx` or install package globally |

## Cursor-Specific Tips

- **.cursorrules**: The plugin provides a template at `.cursorrules`. Copy to your project root and customize.
- **Agent mode**: Use Cmd+I for autonomous multi-file changes with OrgMemory context.
- **Tab completions**: Cursor's tab completion doesn't use OrgMemory — only explicit Chat/Agent prompts access MCP tools.
- **Performance**: OrgMemory queries add latency. For simple tab-completion-style edits, skip OrgMemory context.