import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { McpClient } from '@careloop/client';
import { VOICE_PROFILE } from './alexa/interactionModel';

/** A browser refuses `fetch` unless it is called on the global object: taking the reference
 *  (`const f = fetch`) and calling it later throws "Illegal invocation". Node's fetch accepts any
 *  receiver, so every unit test here kept passing while the simulator could not reach `/mcp` at
 *  all. These tests reproduce the browser rule instead of trusting Node. */
const RESULT = {
  jsonrpc: '2.0', id: 'x',
  result: { protocolVersion: '2025-11-25', serverInfo: { name: 'careloop', version: '6.0.0' }, capabilities: {} },
};

let original: typeof fetch;
let calls: string[];

function installBrowserStyleFetch(): void {
  original = globalThis.fetch;
  calls = [];
  const strict = function (this: unknown, input: any) {
    if (this !== globalThis) {
      throw new TypeError("Failed to execute 'fetch' on 'Window': Illegal invocation");
    }
    calls.push(String(input));
    return Promise.resolve(new Response(JSON.stringify(RESULT), {
      status: 200, headers: { 'content-type': 'application/json', 'MCP-Session-Id': 'cl-test-1' },
    }));
  };
  globalThis.fetch = strict as unknown as typeof fetch;
}

beforeEach(installBrowserStyleFetch);
afterEach(() => { globalThis.fetch = original; });

describe('McpClient fetch binding', () => {
  it('initializes when fetch is only callable on the global', async () => {
    const client = new McpClient({ protocolVersion: '2025-11-25' });
    const result = await client.initialize();
    expect(result.serverInfo.name).toBe('careloop');
    expect(client.sessionId).toBe('cl-test-1');
    expect(calls).toEqual(['/mcp', '/mcp']);
  });

  it('carries the negotiated version and session id on later calls', async () => {
    const client = new McpClient();
    await client.initialize();
    await client.listTools();
    expect(calls).toHaveLength(3);
    expect(client.negotiated).toBe('2025-11-25');
  });

  it('still honours an injected fetch implementation', async () => {
    const seen: string[] = [];
    const injected = ((input: any) => {
      seen.push(String(input));
      return Promise.resolve(new Response(JSON.stringify(RESULT), { status: 200,
        headers: { 'content-type': 'application/json' } }));
    }) as unknown as typeof fetch;
    const client = new McpClient({ fetchImpl: injected, base: 'http://example.test' });
    await client.initialize();
    expect(seen).toEqual(['http://example.test/mcp', 'http://example.test/mcp']);
  });
});

/** Who is speaking is a property of the request, not of any tool's arguments, so the claim rides
 *  `params._meta` — which is why adding it never changed a tool schema. A claim nobody made must not
 *  appear as an empty object either: an absent claim and an unrecognised one are decided differently. */
describe('McpClient speaker claim on the wire', () => {
  const CALL = {
    jsonrpc: '2.0', id: 'x',
    result: { content: [{ type: 'text', text: 'Papa has two items left.' }], structuredContent: {}, isError: false },
  };

  function capturing(holder: { bodies: any[] }): typeof fetch {
    return ((input: any, init: any) => {
      holder.bodies.push(JSON.parse(String(init.body)));
      return Promise.resolve(new Response(JSON.stringify(CALL), {
        status: 200, headers: { 'content-type': 'application/json' },
      }));
    }) as unknown as typeof fetch;
  }

  it('sends _meta only when a speaker is claimed, under the names the artifact declares', async () => {
    const holder: { bodies: any[] } = { bodies: [] };
    const client = new McpClient({ fetchImpl: capturing(holder) });
    await client.callTool('get_care_plan', { person: 'Papa' });
    await client.callTool('get_care_plan', { person: 'Papa' }, {
      [VOICE_PROFILE.speakerMetaKey]: 'Mother',
      [VOICE_PROFILE.profileMetaKey]: VOICE_PROFILE.profileValue,
    });
    const calls = holder.bodies.filter((body) => body.method === 'tools/call');
    expect(calls).toHaveLength(2);
    expect(calls[0].params._meta).toBeUndefined();
    // The key names are the service's own constants, copied into the artifact: a device cannot rename
    // the field the server reads, and a household cannot be claimed into a scope it never stored.
    expect(calls[1].params._meta).toEqual({
      'careloop/speaker': 'Mother',
      'careloop/voiceProfile': 'careloop-simulated/1',
    });
    expect(calls[1].params.arguments).toEqual({ person: 'Papa' });
  });
});
