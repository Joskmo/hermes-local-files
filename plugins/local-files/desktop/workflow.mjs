// Pure orchestration helpers are kept separate for Node contract tests.
const canonical = value => String(value || '').trim()


export function findTargetRoute(
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


export async function createProjectWorkflow({ route, name = '', deps }) {
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


export function describeStatus(status) {
  if (status?.state === 'synced') {
    return { label: 'Синхронизировано', tone: 'success' }
  }
  if (status?.state === 'syncing' || status?.state === 'offline') {
    return { label: 'Синхронизация…', tone: 'progress' }
  }
  return { label: 'Нужно внимание', tone: 'danger' }
}
