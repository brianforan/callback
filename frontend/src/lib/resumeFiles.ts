import type { FileRejection } from '@mantine/dropzone';

// Match the server's default MAX_RESUME_BYTES and MAX_RESUME_PDF_BYTES, so an oversized file is
// caught before upload.
export const MAX_TEX_BYTES = 65_536;
export const MAX_PDF_BYTES = 2_097_152;

export const RESUME_ACCEPT = {
  'text/x-tex': ['.tex'],
  'application/x-tex': ['.tex'],
  'application/pdf': ['.pdf'],
};
export const RESUME_ACCEPT_INPUT = '.tex,.pdf,application/pdf';

export const RESUME_UPLOAD_HINT = 'Upload one .tex file under 64 KB, or one PDF under 2 MB.';
export const PDF_ONLY_NOTE =
  'A PDF is analysed from its text, so sections and dates may be read less exactly than from .tex. It can be matched and used as a baseline, but not edited or tailored.';

/** Why a resume file would be refused, or null when it's fine to upload. */
export function resumeFileProblem(file: File): string | null {
  const name = file.name.toLowerCase();
  if (name.endsWith('.tex')) return file.size > MAX_TEX_BYTES ? 'The .tex file is over 64 KB' : null;
  if (name.endsWith('.pdf')) return file.size > MAX_PDF_BYTES ? 'The PDF is over 2 MB' : null;
  return 'Choose a .tex or .pdf file';
}

/** Dropzone validator: the size limit depends on the kind of file. */
export function resumeDropValidator(file: File) {
  const problem = resumeFileProblem(file);
  return problem ? { code: 'resume-file', message: problem } : null;
}

/** The message for a rejected drop, from the validator when it gave one. */
export function rejectionMessage(rejections: FileRejection[]): string {
  const own = rejections[0]?.errors.find((error) => error.code === 'resume-file');
  return own?.message ?? RESUME_UPLOAD_HINT;
}

const SECTION = /^\\section\{(.*)\}$/;
const ESCAPED = /\\([&%$#_{}])/g;

/** The outline the server read from a PDF, as plain lines: headings, bullets and text. */
export function readableOutline(outline: string): string {
  return outline
    .split('\n')
    .map((line) => {
      const heading = SECTION.exec(line);
      if (heading) return `\n${heading[1].replace(ESCAPED, '$1').toUpperCase()}`;
      const text = line.replace(ESCAPED, '$1');
      return text.startsWith('\\item ') ? `  • ${text.slice(6)}` : text;
    })
    .join('\n')
    .trim();
}
