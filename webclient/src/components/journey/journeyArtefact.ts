import type { JourneyTemplateUpload } from '../../data/apiClient'

/**
 * Parse a practitioner's journey artefact before it is sent.
 *
 * The API validates the shape properly (unique stage keys, check types this
 * deployment can evaluate) and answers 422 with a reason. This only catches
 * what would otherwise arrive as an opaque validation error: not JSON, not an
 * object, or no stages at all. Everything else is the server's call.
 */
export function readJourneyArtefact(text: string): JourneyTemplateUpload {
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    throw new Error('the file is not valid JSON')
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('the file must contain a journey object')
  }
  const stages = (parsed as { stages?: unknown }).stages
  if (!Array.isArray(stages) || stages.length === 0) {
    throw new Error('the file has no stages')
  }
  for (const [i, stage] of stages.entries()) {
    if (!stage || typeof stage !== 'object' || typeof (stage as { key?: unknown }).key !== 'string' || typeof (stage as { title?: unknown }).title !== 'string') {
      throw new Error(`stage ${i + 1} needs a key and a title`)
    }
  }
  return parsed as JourneyTemplateUpload
}

/** File text, via `Blob.text()` where the runtime has it and `FileReader` where it does not. */
export function readFileText(file: File): Promise<string> {
  if (typeof (file as { text?: unknown }).text === 'function') return file.text()
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result ?? ''))
    reader.onerror = () => reject(reader.error ?? new Error('the file could not be read'))
    reader.readAsText(file)
  })
}
