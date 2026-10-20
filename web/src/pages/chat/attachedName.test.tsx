import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { forwardRef, type ReactNode } from 'react'

// Opening an attachment's original file (its sent chip → Open original file) shows it in the chat's
// file panel, titled `60f4451fe3a74ede8fdbd528e348197c_image.png` above a preview of the image: the
// upload route's stored name. The panel and the real `FileViewer` render here; only the surface
// that previews the bytes is replaced, by one that shows the title and header chrome it is handed.
vi.mock('../../ui/content/ContentSurface', () => ({
  ContentSurface: forwardRef(function ContentSurface(
    { title, headerLeft, headerExtras }: { title: string; headerLeft: ReactNode; headerExtras: ReactNode },
    _ref,
  ) {
    return (
      <div data-testid="surface" data-title={title}>
        <div data-testid="header">{headerLeft}</div>
        {headerExtras}
      </div>
    )
  }),
}))

import { ChatFilePanel } from './ChatFilePanel'
import { attachedName } from '../files/fileMeta'
import { registerBuiltinContentTypes } from '../../ui/content/registerBuiltins'

// The viewer resolves an image's content type from the registry the app fills at boot.
registerBuiltinContentTypes()

const STORED = '/home/u/.personalclaw/uploads/60f4451fe3a74ede8fdbd528e348197c_image.png'

afterEach(cleanup)

describe('an attachment is named as it was attached', () => {
  it("drops the upload route's prefix, and only from a file it stored", () => {
    expect(attachedName(STORED)).toBe('image.png')
    expect(attachedName('/home/u/.personalclaw/uploads/notes.md')).toBe('notes.md')
    expect(attachedName('/work/src/60f4451fe3a74ede8fdbd528e348197c_image.png'))
      .toBe('60f4451fe3a74ede8fdbd528e348197c_image.png')
    expect(attachedName('/home/u/.personalclaw/screenshots/screenshot_1790.png')).toBe('screenshot_1790.png')
  })

  it("titles the file panel's preview with that name, and downloads it by that name", () => {
    render(<ChatFilePanel path={STORED} onClose={() => {}} />)

    expect(screen.getByTestId('header').textContent).toContain('image.png')
    expect(screen.getByTestId('header').textContent).not.toContain('60f4451fe3a74ede8fdbd528e348197c')
    expect(screen.getByTestId('surface').getAttribute('data-title')).toBe('image.png')
    expect(screen.getByTitle('Download').getAttribute('download')).toBe('image.png')
  })

  it('keeps the stored path in the location row, as its tooltip', () => {
    render(<ChatFilePanel path={STORED} onClose={() => {}} />)

    const row = screen.getByTitle(STORED)
    expect(row.textContent).toContain('image.png')
    expect(row.textContent).not.toContain('60f4451fe3a74ede8fdbd528e348197c')
  })
})
