const DATABASE_NAME = 'workcode-local-recordings'
const DATABASE_VERSION = 1
const RECORDINGS_STORE = 'recordings'
const CHUNKS_STORE = 'chunks'

let databasePromise

function openDatabase() {
  if (databasePromise) return databasePromise

  databasePromise = new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE_NAME, DATABASE_VERSION)

    request.onupgradeneeded = () => {
      const database = request.result
      if (!database.objectStoreNames.contains(RECORDINGS_STORE)) {
        database.createObjectStore(RECORDINGS_STORE, { keyPath: 'id' })
      }
      if (!database.objectStoreNames.contains(CHUNKS_STORE)) {
        const chunks = database.createObjectStore(CHUNKS_STORE, {
          keyPath: ['recording_id', 'sequence'],
        })
        chunks.createIndex('recording_id', 'recording_id', { unique: false })
      }
    }

    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error || new Error('Could not open local recording storage'))
    request.onblocked = () => reject(new Error('Local recording storage upgrade is blocked'))
  }).catch((error) => {
    databasePromise = null
    throw error
  })

  return databasePromise
}

function requestResult(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error || new Error('Local recording storage request failed'))
  })
}

function transactionDone(transaction) {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = resolve
    transaction.onerror = () => reject(transaction.error || new Error('Local recording storage transaction failed'))
    transaction.onabort = () => reject(transaction.error || new Error('Local recording storage transaction was aborted'))
  })
}

function newId() {
  return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`
}

export const LocalRecordingStorage = {
  async create(metadata) {
    const database = await openDatabase()
    const recording = {
      id: newId(),
      size_bytes: 0,
      chunk_count: 0,
      sync_status: 'pending',
      status: 'recording',
      ...metadata,
    }
    const transaction = database.transaction(RECORDINGS_STORE, 'readwrite')
    transaction.objectStore(RECORDINGS_STORE).put(recording)
    await transactionDone(transaction)
    return recording
  },

  async appendChunk(recordingId, sequence, blob) {
    const database = await openDatabase()
    const transaction = database.transaction([RECORDINGS_STORE, CHUNKS_STORE], 'readwrite')
    const recordings = transaction.objectStore(RECORDINGS_STORE)
    const chunks = transaction.objectStore(CHUNKS_STORE)
    const key = [recordingId, sequence]

    const previousRequest = chunks.get(key)
    const recordingRequest = recordings.get(recordingId)
    const previousResult = requestResult(previousRequest)
    const recordingResult = requestResult(recordingRequest)
    const [previous, recording] = await Promise.all([previousResult, recordingResult])
    if (!recording) throw new Error('Local recording metadata was not found')

    chunks.put({ recording_id: recordingId, sequence, blob, size_bytes: blob.size })
    recording.size_bytes += blob.size - (previous?.size_bytes || 0)
    if (!previous) recording.chunk_count += 1
    recordings.put(recording)
    await transactionDone(transaction)
  },

  async get(recordingId) {
    const database = await openDatabase()
    const transaction = database.transaction(RECORDINGS_STORE, 'readonly')
    return requestResult(transaction.objectStore(RECORDINGS_STORE).get(recordingId))
  },

  async list(userId) {
    const database = await openDatabase()
    const transaction = database.transaction(RECORDINGS_STORE, 'readonly')
    const recordings = await requestResult(transaction.objectStore(RECORDINGS_STORE).getAll())
    return recordings
      .filter((recording) => recording.user_id === userId && recording.status !== 'deleted')
      .sort((left, right) => right.started_at.localeCompare(left.started_at))
  },

  async chunks(recordingId) {
    const database = await openDatabase()
    const transaction = database.transaction(CHUNKS_STORE, 'readonly')
    const rows = await requestResult(
      transaction.objectStore(CHUNKS_STORE).index('recording_id').getAll(recordingId),
    )
    return rows.sort((left, right) => left.sequence - right.sequence)
  },

  async makeBlob(recordingId) {
    const recording = await this.get(recordingId)
    if (!recording) throw new Error('Recording was not found')
    const chunks = await this.chunks(recordingId)
    return new Blob(chunks.map((chunk) => chunk.blob), {
      type: recording.mime_type || 'video/webm',
    })
  },

  async update(recordingId, changes) {
    const database = await openDatabase()
    const transaction = database.transaction(RECORDINGS_STORE, 'readwrite')
    const store = transaction.objectStore(RECORDINGS_STORE)
    const request = store.get(recordingId)
    request.onsuccess = () => {
      if (!request.result) {
        transaction.abort()
        return
      }
      store.put({ ...request.result, ...changes })
    }
    await transactionDone(transaction)
  },

  async remove(recordingId) {
    const database = await openDatabase()
    const transaction = database.transaction([RECORDINGS_STORE, CHUNKS_STORE], 'readwrite')
    transaction.objectStore(RECORDINGS_STORE).delete(recordingId)
    const chunks = transaction.objectStore(CHUNKS_STORE)
    const cursorRequest = chunks.index('recording_id').openCursor(recordingId)
    cursorRequest.onsuccess = () => {
      const cursor = cursorRequest.result
      if (cursor) {
        cursor.delete()
        cursor.continue()
      }
    }
    await transactionDone(transaction)
  },
}
