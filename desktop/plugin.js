import {
  PALETTE_AREA,
  PANES_AREA,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  STATUSBAR_AREAS,
  host,
  useValue,
} from '@hermes/plugin-sdk'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const COMPANION_ORIGIN = 'http://127.0.0.1:45671'
const COMPANION_TOKEN = '__COMPANION_TOKEN__'
const PREFERRED_CONNECTION_ID = '__TARGET_CONNECTION_ID__'
const TARGET_PROFILE = '__TARGET_PROFILE__'
const POLL_MS = 4000

// Pure orchestration helpers are kept separate for Node contract tests.
const canonical = value => String(value || '').trim()


function findTargetRoute(
  routes,
  connections,
  targetProfile,
  preferredConnectionId = '',
) {
  const profile = canonical(targetProfile)
  if (!profile) throw new Error('A target Hermes profile is required.')
  const preferred = canonical(preferredConnectionId)
  const sshIds = new Set(
    connections
      .filter(connection => connection.kind === 'ssh' || connection.type === 'ssh')
      .map(connection => canonical(connection.id)),
  )
  const candidates = routes.filter(route => (
    canonical(route.targetProfile || route.profile) === profile
    && route.mode === 'remote'
    && sshIds.has(canonical(route.connectionId))
  ))
  if (preferred) {
    const selected = candidates.find(route => canonical(route.connectionId) === preferred)
    if (selected) return selected
  }
  if (candidates.length === 1) return candidates[0]
  if (!candidates.length) {
    throw new Error(`Профиль ${profile} на выбранном SSH-сервере не найден.`)
  }
  throw new Error('Укажите точный connection ID при установке плагина.')
}


function assertServerContract(server, mappingId) {
  const required = ['mapping_id', 'name', 'folder_id', 'server_path', 'server_device_id']
  for (const key of required) {
    if (!canonical(server?.[key])) throw new Error(`Server response is missing ${key}.`)
  }
  if (server.mapping_id !== mappingId) throw new Error('Server returned a different mapping id.')
}


function projectPath(project) {
  return canonical(project?.primary_path)
    || canonical(project?.folders?.find(folder => folder.is_primary)?.path)
    || canonical(project?.folders?.[0]?.path)
}


async function createProjectWorkflow({ route, name = '', deps }) {
  if (!canonical(route?.connectionId) || !canonical(route?.targetProfile || route?.profile)) {
    throw new Error('A connection-qualified profile route is required.')
  }
  const picked = await deps.pickFolder()
  const projectName = canonical(name) || canonical(picked?.suggested_name)
  if (!canonical(picked?.mapping_id) || !canonical(picked?.local_path)
      || !canonical(picked?.local_device_id) || !projectName) {
    throw new Error('Local folder selection is incomplete.')
  }

  const server = await deps.provisionServer({
    mapping_id: picked.mapping_id,
    name: projectName,
    local_device_id: picked.local_device_id,
  })
  assertServerContract(server, picked.mapping_id)

  const mapping = await deps.provisionLocal(picked.local_path, server)
  const sync = await deps.waitForStableSync(server.folder_id)
  if (sync?.state !== 'synced') {
    throw new Error('Initial synchronization did not complete safely.')
  }

  const profile = canonical(route.targetProfile || route.profile)
  const params = {
    profile,
    name: projectName,
    folders: [server.server_path],
    primary_path: server.server_path,
    use: true,
  }
  const created = await deps.requestProfile(route, 'projects.create', params)
  const createdProject = created?.project
  if (!createdProject?.id || projectPath(createdProject) !== server.server_path) {
    throw new Error('Hermes returned an unexpected project path.')
  }

  const listed = await deps.requestProfile(route, 'projects.list', { profile })
  const verified = listed?.projects?.find(project => project.id === createdProject.id)
  if (!verified || projectPath(verified) !== server.server_path) {
    throw new Error('Created Hermes project could not be verified.')
  }
  return { project: createdProject, mapping: { ...server, ...mapping, ...picked }, sync }
}


function describeStatus(status) {
  if (status?.state === 'synced') {
    return { label: 'Синхронизировано', tone: 'success' }
  }
  if (status?.state === 'syncing' || status?.state === 'offline') {
    return { label: 'Синхронизация…', tone: 'progress' }
  }
  return { label: 'Нужно внимание', tone: 'danger' }
}

function sleep(ms) {
  return new Promise(resolve => window.setTimeout(resolve, ms))
}

function cleanError(error) {
  const message = String(error?.message || error || 'Неизвестная ошибка')
  return message.replace(/Bearer\s+\S+/gi, 'Bearer ***').slice(0, 300)
}

function createApi(ctx) {
  const local = async (path, { method = 'GET', body } = {}) => {
    const response = await fetch(`${COMPANION_ORIGIN}${path}`, {
      method,
      headers: {
        'X-Hermes-Local-Files-Token': COMPANION_TOKEN,
        ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: 'no-store',
    })
    const payload = await response.json().catch(() => ({}))
    if (!response.ok) throw new Error(payload.error || `Local companion: HTTP ${response.status}`)
    return payload
  }

  return {
    list: () => local('/v1/projects'),
    pickFolder: () => local('/v1/pick-folder', { method: 'POST', body: {} }),
    status: folderId => local(`/v1/status?folder_id=${encodeURIComponent(folderId)}`),
    provisionLocal: (localPath, server) => local('/v1/provision-local', {
      method: 'POST',
      body: { local_path: localPath, server },
    }),
    provisionServer: input => ctx.rest('/v1/provision', { method: 'POST', body: input }),
  }
}

async function waitForStableSync(api, folderId, onStatus) {
  const deadline = Date.now() + 15 * 60 * 1000
  let consecutive = 0
  while (Date.now() < deadline) {
    const status = await api.status(folderId)
    onStatus(status)
    if (status.state === 'attention') return status
    consecutive = status.state === 'synced' ? consecutive + 1 : 0
    if (consecutive >= 2) return status
    await sleep(POLL_MS)
  }
  return { state: 'attention', error: 'Первичная синхронизация не завершилась вовремя.' }
}

function StatusPill({ status }) {
  const display = describeStatus(status)
  const classes = display.tone === 'success'
    ? 'bg-emerald-500/15 text-emerald-500'
    : display.tone === 'danger'
      ? 'bg-red-500/15 text-red-500'
      : 'bg-amber-500/15 text-amber-500'
  return jsx('span', {
    className: `rounded-full px-2 py-0.5 text-[0.6875rem] font-medium ${classes}`,
    children: display.label,
  })
}

function ProjectRow({ ctx, project, status }) {
  return jsxs('div', {
    className: 'flex items-center gap-3 rounded-lg border border-(--ui-border) px-3 py-2.5',
    children: [
      jsx('span', { className: 'codicon codicon-folder text-(--ui-text-tertiary)' }),
      jsxs('div', {
        className: 'min-w-0 flex-1',
        children: [
          jsx('div', { className: 'truncate font-medium', children: project.name }),
          jsx('div', {
            className: 'truncate text-xs text-(--ui-text-tertiary)',
            children: project.local_path,
          }),
        ],
      }),
      jsx(StatusPill, { status }),
      jsx('button', {
        type: 'button',
        className: 'rounded p-1 text-(--ui-text-tertiary) hover:bg-(--ui-hover)',
        title: 'Показать в Finder',
        onClick: () => void ctx.os.revealPath(project.local_path),
        children: jsx('span', { className: 'codicon codicon-folder-opened' }),
      }),
    ],
  })
}

function LocalFilesPage({ ctx }) {
  const gateway = useValue(host.state.gateway)
  const api = useMemo(() => createApi(ctx), [ctx])
  const [projects, setProjects] = useState([])
  const [statuses, setStatuses] = useState({})
  const [busy, setBusy] = useState(false)
  const [phase, setPhase] = useState('')
  const [error, setError] = useState('')

  const refresh = useCallback(async () => {
    try {
      const result = await api.list()
      const rows = Array.isArray(result.projects) ? result.projects : []
      setProjects(rows)
      const values = await Promise.all(rows.map(async project => {
        try {
          return [project.folder_id, await api.status(project.folder_id)]
        } catch {
          return [project.folder_id, { state: 'attention' }]
        }
      }))
      setStatuses(Object.fromEntries(values))
      setError('')
    } catch (refreshError) {
      setError('Служба локальных файлов не запущена. Перезапустите Hermes или войдите в macOS.')
    }
  }, [api])

  useEffect(() => {
    void refresh()
    const timer = window.setInterval(() => void refresh(), POLL_MS)
    return () => window.clearInterval(timer)
  }, [refresh])

  const addProject = async () => {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      setPhase('Выбор папки…')
      const connections = await host.connections()
      const routes = await host.profileRoutes()
      const route = findTargetRoute(
        routes,
        connections,
        TARGET_PROFILE,
        PREFERRED_CONNECTION_ID,
      )
      await host.ensureAgent(route.connectionId, route.profile)
      const result = await createProjectWorkflow({
        route,
        deps: {
          pickFolder: api.pickFolder,
          provisionServer: async input => {
            setPhase('Подготовка проекта на сервере…')
            return api.provisionServer(input)
          },
          provisionLocal: async (path, server) => {
            setPhase('Подключение автоматической синхронизации…')
            return api.provisionLocal(path, server)
          },
          waitForStableSync: folderId => {
            setPhase('Первая синхронизация…')
            return waitForStableSync(api, folderId, status => {
              setStatuses(current => ({ ...current, [folderId]: status }))
            })
          },
          requestProfile: (target, method, params) => host.requestProfile(target, method, params),
        },
      })
      host.notify({ kind: 'success', message: `Проект «${result.project.name}» готов.` })
      setPhase('')
      await refresh()
    } catch (addError) {
      const message = cleanError(addError)
      if (!/cancel/i.test(message)) {
        setError(message)
        host.notifyError(message)
      }
    } finally {
      setBusy(false)
      setPhase('')
    }
  }

  return jsxs('div', {
    className: 'flex h-full flex-col overflow-hidden',
    children: [
      jsxs('header', {
        className: 'flex items-center gap-3 border-b border-(--ui-border) px-5 py-4',
        children: [
          jsxs('div', {
            className: 'min-w-0 flex-1',
            children: [
              jsx('h1', { className: 'text-base font-semibold', children: 'Файлы на этом Mac' }),
              jsx('p', {
                className: 'mt-0.5 text-xs text-(--ui-text-tertiary)',
                children: 'Обычные папки Finder, автоматически доступные удалённому Hermes.',
              }),
            ],
          }),
          jsx('button', {
            type: 'button',
            disabled: busy || gateway !== 'open',
            onClick: () => void addProject(),
            className: 'rounded-md bg-(--ui-accent) px-3 py-1.5 text-sm font-medium text-(--ui-accent-foreground) disabled:opacity-50',
            children: busy ? phase || 'Подождите…' : 'Добавить папку',
          }),
        ],
      }),
      jsxs('main', {
        className: 'flex-1 overflow-auto p-5',
        children: [
          error ? jsxs('div', {
            className: 'mb-4 rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-500',
            children: [
              jsx('div', { children: error }),
              jsx('button', {
                type: 'button',
                className: 'mt-2 underline',
                onClick: () => void refresh(),
                children: 'Проверить снова',
              }),
            ],
          }) : null,
          projects.length
            ? jsx('div', {
              className: 'grid gap-2',
              children: projects.map(project => jsx(ProjectRow, {
                ctx,
                project,
                status: statuses[project.folder_id] || { state: 'syncing' },
              }, project.mapping_id)),
            })
            : !error
              ? jsxs('div', {
                className: 'flex min-h-56 flex-col items-center justify-center rounded-xl border border-dashed border-(--ui-border) text-center',
                children: [
                  jsx('span', { className: 'codicon codicon-folder-library mb-3 text-2xl text-(--ui-text-tertiary)' }),
                  jsx('div', { className: 'font-medium', children: 'Пока нет локальных проектов' }),
                  jsx('div', {
                    className: 'mt-1 max-w-sm text-xs text-(--ui-text-tertiary)',
                    children: 'Выберите папку один раз. После этого изменения будут переноситься в обе стороны автоматически.',
                  }),
                ],
              })
              : null,
        ],
      }),
    ],
  })
}

function StatusItem({ ctx }) {
  const [status, setStatus] = useState({ state: 'syncing' })
  useEffect(() => {
    let active = true
    const update = async () => {
      try {
        const api = createApi(ctx)
        const { projects } = await api.list()
        const states = await Promise.all((projects || []).map(project => api.status(project.folder_id)))
        const combined = states.some(item => item.state === 'attention')
          ? { state: 'attention' }
          : states.some(item => item.state !== 'synced')
            ? { state: 'syncing' }
            : { state: 'synced' }
        if (active) setStatus(combined)
      } catch {
        if (active) setStatus({ state: 'attention' })
      }
    }
    void update()
    const timer = window.setInterval(() => void update(), 10000)
    return () => { active = false; window.clearInterval(timer) }
  }, [ctx])
  const display = describeStatus(status)
  return jsx('button', {
    type: 'button',
    className: 'flex items-center gap-1 px-1.5 text-[0.6875rem] text-(--ui-text-tertiary)',
    onClick: () => host.navigate('/local-files'),
    title: display.label,
    children: [
      jsx('span', { className: `codicon ${status.state === 'synced' ? 'codicon-pass-filled' : 'codicon-sync'}` }),
      jsx('span', { children: 'Local Files' }),
    ],
  })
}

export default {
  id: 'local-files',
  name: 'Local Files',
  defaultEnabled: true,
  register(ctx) {
    ctx.registerMany([
      {
        id: 'pane',
        area: PANES_AREA,
        title: 'Local Files',
        data: { placement: 'right', width: '420px' },
        render: () => jsx(LocalFilesPage, { ctx }),
      },
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: '/local-files' },
        render: () => jsx(LocalFilesPage, { ctx }),
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        data: { path: '/local-files', label: 'Local Files', codicon: 'folder-library' },
      },
      {
        id: 'status',
        area: STATUSBAR_AREAS.right,
        order: 115,
        render: () => jsx(StatusItem, { ctx }),
      },
      {
        id: 'open',
        area: PALETTE_AREA,
        data: {
          id: 'local-files.open',
          label: 'Открыть Local Files',
          keywords: ['local', 'files', 'папка', 'файлы'],
          run: () => host.navigate('/local-files'),
        },
      },
    ])
  },
}
