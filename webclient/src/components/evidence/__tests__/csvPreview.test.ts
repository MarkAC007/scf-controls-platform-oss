// SPDX-License-Identifier: AGPL-3.0-only
/**
 * csvPreview — a small RFC 4180 reader for the evidence viewer (#1309).
 *
 * Access reviews and user lists are the CSVs auditors open most, and they are
 * exactly the files that carry commas inside names and line breaks inside
 * comments. A split-on-comma reader would shift every column after the first
 * such cell, which in a review table means a person's row reads as someone
 * else's access.
 */
import { describe, it, expect } from 'vitest'

import { parseCsv } from '../csvPreview'

describe('parseCsv', () => {
  it('splits plain rows and fields', () => {
    expect(parseCsv('a,b,c\n1,2,3')).toEqual([['a', 'b', 'c'], ['1', '2', '3']])
  })

  it('does not treat a trailing newline as an extra row', () => {
    expect(parseCsv('a,b\n1,2\n')).toEqual([['a', 'b'], ['1', '2']])
    expect(parseCsv('a,b\r\n1,2\r\n')).toEqual([['a', 'b'], ['1', '2']])
  })

  it('reads CRLF and LF line endings alike', () => {
    expect(parseCsv('a,b\r\n1,2\n3,4')).toEqual([['a', 'b'], ['1', '2'], ['3', '4']])
  })

  it('keeps commas inside quoted fields', () => {
    expect(parseCsv('name,role\n"Smith, Jane",admin')).toEqual([
      ['name', 'role'],
      ['Smith, Jane', 'admin'],
    ])
  })

  it('turns a doubled quote inside a quoted field into one quote', () => {
    expect(parseCsv('note\n"said ""yes"" twice"')).toEqual([['note'], ['said "yes" twice']])
  })

  it('keeps line breaks inside quoted fields as part of the cell', () => {
    expect(parseCsv('id,comment\n1,"line one\nline two"\n2,"a\r\nb"\n')).toEqual([
      ['id', 'comment'],
      ['1', 'line one\nline two'],
      ['2', 'a\r\nb'],
    ])
  })

  it('keeps empty fields, including a trailing one', () => {
    expect(parseCsv('a,,c,\n,,,')).toEqual([['a', '', 'c', ''], ['', '', '', '']])
  })

  it('keeps an empty quoted field', () => {
    expect(parseCsv('a,"",c')).toEqual([['a', '', 'c']])
  })

  it('drops a leading byte order mark', () => {
    expect(parseCsv('﻿a,b\n1,2')).toEqual([['a', 'b'], ['1', '2']])
  })

  it('returns no rows for empty input', () => {
    expect(parseCsv('')).toEqual([])
    expect(parseCsv('\n')).toEqual([])
  })

  it('reads an unterminated quote to the end rather than throwing', () => {
    expect(parseCsv('a,"open\nstill open')).toEqual([['a', 'open\nstill open']])
  })
})
