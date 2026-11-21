// SPDX-License-Identifier: AGPL-3.0-only
import type { EvidenceFileTypesResponse } from '../../../data/apiClient'

/**
 * The `GET /api/evidence-file-types` reply, as the backend contract for #1309
 * states it. Tests only: the page itself must never carry a copy of this list.
 */
export function makeEvidenceFileTypes(
  overrides: Partial<EvidenceFileTypesResponse> = {},
): EvidenceFileTypesResponse {
  return {
    max_file_size_bytes: 52428800,
    types: [
      { content_type: 'application/pdf', label: 'PDF', extensions: ['.pdf'], preview: 'pdf', ai_readable: true },
      { content_type: 'image/png', label: 'PNG image', extensions: ['.png'], preview: 'image', ai_readable: true },
      { content_type: 'image/jpeg', label: 'JPEG image', extensions: ['.jpg', '.jpeg'], preview: 'image', ai_readable: true },
      { content_type: 'image/gif', label: 'GIF image', extensions: ['.gif'], preview: 'image', ai_readable: true },
      { content_type: 'image/webp', label: 'WebP image', extensions: ['.webp'], preview: 'image', ai_readable: true },
      { content_type: 'text/csv', label: 'CSV', extensions: ['.csv'], preview: 'csv', ai_readable: true },
      { content_type: 'text/plain', label: 'Plain text', extensions: ['.txt', '.log'], preview: 'code', ai_readable: true },
      { content_type: 'text/markdown', label: 'Markdown', extensions: ['.md', '.markdown'], preview: 'markdown', ai_readable: true },
      { content_type: 'text/yaml', label: 'YAML', extensions: ['.yml', '.yaml'], preview: 'code', ai_readable: true },
      { content_type: 'application/json', label: 'JSON', extensions: ['.json'], preview: 'code', ai_readable: true },
      { content_type: 'application/xml', label: 'XML', extensions: ['.xml'], preview: 'code', ai_readable: true },
      {
        content_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        label: 'Word document', extensions: ['.docx'], preview: 'structured', ai_readable: true,
      },
      {
        content_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        label: 'Excel workbook', extensions: ['.xlsx'], preview: 'structured', ai_readable: true,
      },
      {
        content_type: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        label: 'PowerPoint presentation', extensions: ['.pptx'], preview: 'structured', ai_readable: true,
      },
      { content_type: 'application/zip', label: 'ZIP archive', extensions: ['.zip'], preview: 'structured', ai_readable: true },
      { content_type: 'message/rfc822', label: 'Email message', extensions: ['.eml'], preview: 'structured', ai_readable: true },
    ],
    ...overrides,
  }
}
