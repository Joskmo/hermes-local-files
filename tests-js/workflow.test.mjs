import assert from 'node:assert/strict'
import test from 'node:test'

import {
  createProjectWorkflow,
  describeStatus,
  findTargetRoute,
} from '../plugins/local-files/desktop/workflow.mjs'


test('findTargetRoute binds the named SSH connection and target profile', () => {
  const routes = [
    { connectionId: 'local', mode: 'local', profile: 'work', targetProfile: 'work' },
    { connectionId: 'server', mode: 'remote', profile: 'work', targetProfile: 'work' },
  ]
  const connections = [
    { id: 'server', kind: 'ssh', label: 'Remote server' },
    { id: 'other', kind: 'ssh', label: 'Other' },
  ]

  assert.deepEqual(findTargetRoute(routes, connections, 'work', 'server'), routes[1])
})


test('create workflow provisions server, then local, waits, creates and verifies project', async () => {
  const calls = []
  const route = {
    connectionId: 'server',
    mode: 'remote',
    profile: 'work',
    targetProfile: 'work',
  }
  const deps = {
    pickFolder: async () => ({
      mapping_id: 'mapping-1',
      local_path: '/Users/example/Documents/Family',
      local_device_id: 'MAC-ID',
      suggested_name: 'Family',
    }),
    provisionServer: async input => {
      calls.push(['server', input])
      return {
        mapping_id: input.mapping_id,
        name: input.name,
        folder_id: 'hermes-folder',
        server_path: '/srv/hermes-local-files/work/projects/mapping-1-family',
        server_device_id: 'SERVER-ID',
      }
    },
    provisionLocal: async (localPath, server) => {
      calls.push(['local', localPath, server])
      return { ok: true }
    },
    waitForStableSync: async folderId => {
      calls.push(['wait', folderId])
      return { state: 'synced' }
    },
    requestProfile: async (actualRoute, method, params) => {
      calls.push(['rpc', actualRoute, method, params])
      if (method === 'projects.create') {
        return {
          project: {
            id: 'p1',
            name: 'Family',
            primary_path: '/srv/hermes-local-files/work/projects/mapping-1-family',
            folders: [{ path: '/srv/hermes-local-files/work/projects/mapping-1-family' }],
          },
        }
      }
      return {
        projects: [{
          id: 'p1',
          name: 'Family',
          primary_path: '/srv/hermes-local-files/work/projects/mapping-1-family',
        }],
      }
    },
  }

  const result = await createProjectWorkflow({ route, name: '', deps })

  assert.equal(result.project.id, 'p1')
  assert.equal(result.mapping.local_path, '/Users/example/Documents/Family')
  assert.deepEqual(calls.map(call => call[0]), ['server', 'local', 'wait', 'rpc', 'rpc'])
  const create = calls[3]
  assert.equal(create[1], route)
  assert.equal(create[2], 'projects.create')
  assert.deepEqual(create[3], {
    profile: 'work',
    name: 'Family',
    folders: ['/srv/hermes-local-files/work/projects/mapping-1-family'],
    primary_path: '/srv/hermes-local-files/work/projects/mapping-1-family',
    use: true,
  })
})


test('create workflow refuses to register a project before stable sync', async () => {
  let rpcCalled = false
  const deps = {
    pickFolder: async () => ({
      mapping_id: 'mapping-1',
      local_path: '/tmp/demo',
      local_device_id: 'MAC-ID',
      suggested_name: 'Demo',
    }),
    provisionServer: async () => ({
      mapping_id: 'mapping-1',
      name: 'Demo',
      folder_id: 'folder',
      server_path: '/srv/demo',
      server_device_id: 'SERVER-ID',
    }),
    provisionLocal: async () => ({}),
    waitForStableSync: async () => ({ state: 'syncing' }),
    requestProfile: async () => {
      rpcCalled = true
      return {}
    },
  }

  await assert.rejects(
    createProjectWorkflow({
      route: { connectionId: 'server', profile: 'work', targetProfile: 'work' },
      deps,
    }),
    /initial synchronization/i,
  )
  assert.equal(rpcCalled, false)
})


test('status copy is simple and actionable', () => {
  assert.deepEqual(describeStatus({ state: 'synced' }), {
    label: 'Синхронизировано',
    tone: 'success',
  })
  assert.equal(describeStatus({ state: 'syncing' }).label, 'Синхронизация…')
  assert.equal(describeStatus({ state: 'attention' }).label, 'Нужно внимание')
})
