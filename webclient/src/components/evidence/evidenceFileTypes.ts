// SPDX-License-Identifier: AGPL-3.0-only
/**
 * Pure helpers over the server's evidence file-type list (#1309).
 *
 * Nothing here knows any particular type. Every answer is read from the list
 * the server sent, so a type added to the backend reaches the upload control
 * and the viewer with no edit on this side.
 */
import type { EvidenceFilePreviewMode, EvidenceFileTypeInfo } from '../../data/apiClient'

/** `.log` for "Server.LOG"; null when the name has no extension. */
export function extensionOf(filename: string): string | null {
  const dot = filename.lastIndexOf('.')
  if (dot <= 0 || dot === filename.length - 1) return null
  return filename.slice(dot).toLowerCase()
}

export function typeForExtension(
  types: EvidenceFileTypeInfo[],
  filename: string,
): EvidenceFileTypeInfo | undefined {
  const ext = extensionOf(filename)
  if (!ext) return undefined
  return types.find(t => t.extensions.some(e => e.toLowerCase() === ext))
}

export function typeForContentType(
  types: EvidenceFileTypeInfo[],
  contentType: string | null | undefined,
): EvidenceFileTypeInfo | undefined {
  if (!contentType) return undefined
  return types.find(t => t.content_type === contentType)
}

/**
 * The listed type a file to be uploaded is, if any.
 *
 * By extension only. Browsers disagree about types (a `.md` arrives as '',
 * `text/x-markdown` or `text/markdown`; a `.log` usually as ''), and the
 * server stores a file under the type its name gives, not the one the browser
 * reported. A file whose name has no listed extension (`sshd.conf` arriving
 * as `text/plain`, `photo.jfif` as `image/jpeg`) is therefore not accepted:
 * sent anyway, it would be stored as an unknown type and quarantined.
 */
export function listedTypeOfFile(
  types: EvidenceFileTypeInfo[],
  file: { name: string },
): EvidenceFileTypeInfo | undefined {
  return typeForExtension(types, file.name)
}

/** For the picker: every content type, then every extension, comma separated. */
export function acceptAttribute(types: EvidenceFileTypeInfo[]): string {
  return [
    ...types.map(t => t.content_type),
    ...types.flatMap(t => t.extensions),
  ].join(',')
}

/** Each label once, in list order. */
export function uniqueLabels(types: EvidenceFileTypeInfo[]): string[] {
  return Array.from(new Set(types.map(t => t.label)))
}

/**
 * The viewer for a stored file, by the server's list: its content type, then
 * its extension. Undefined when the list does not place it.
 */
export function listedPreviewMode(
  types: EvidenceFileTypeInfo[],
  file: { content_type: string | null; filename: string },
): EvidenceFilePreviewMode | undefined {
  return (typeForContentType(types, file.content_type) ?? typeForExtension(types, file.filename))?.preview
}
