import type { Application } from './types';

export function pct(rate: number): string {
  return `${Math.round(rate * 100)}%`;
}

/** US dollars to the cent, with a floor so a tiny nonzero cost doesn't read as free. */
export function formatUsd(value: number): string {
  return value < 0.01 && value > 0 ? '<$0.01' : `$${value.toFixed(2)}`;
}

function parseDay(day: string): Date {
  // Full timestamps are converted to local time; bare YYYY-MM-DD dates must not shift across zones.
  if (day.length > 10) return new Date(day);
  const [year, month, date] = day.split('-').map(Number);
  return new Date(year, month - 1, date);
}

export function formatDay(day: string): string {
  return parseDay(day).toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

export function formatWeek(day: string): string {
  return parseDay(day).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

export function formatDateTime(timestamp: string): string {
  return new Date(timestamp).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

export function todayIso(): string {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const date = String(now.getDate()).padStart(2, '0');
  return `${now.getFullYear()}-${month}-${date}`;
}

export function daysAgo(day: string): string {
  const days = Math.round((parseDay(todayIso()).getTime() - parseDay(day).getTime()) / 86_400_000);
  if (days <= 0) return 'today';
  return days === 1 ? '1 day ago' : `${days} days ago`;
}

function csvCell(value: string | null): string {
  const text = value ?? '';
  // A leading =, +, - or @ would run as a formula when the file is opened in a spreadsheet.
  const safe = /^[=+\-@\t\r]/.test(text) ? `'${text}` : text;
  return `"${safe.replaceAll('"', '""')}"`;
}

export function saveTextFile(filename: string, contents: string, type: string) {
  const url = URL.createObjectURL(new Blob([contents], { type }));
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

/** Download a file the server serves as an attachment, such as a resume's PDF. */
export function downloadFile(href: string) {
  const link = document.createElement('a');
  link.href = href;
  link.download = '';
  document.body.append(link);
  link.click();
  link.remove();
}

export function exportApplicationsCsv(applications: Application[]) {
  const header = ['Company', 'Role', 'Status', 'Applied', 'Location', 'Source', 'Resume', 'URL', 'Notes'];
  const rows = applications.map((application) =>
    [
      application.company,
      application.role_title,
      application.status,
      application.applied_on,
      application.location,
      application.source,
      application.resume_label,
      application.job_url,
      application.notes,
    ]
      .map(csvCell)
      .join(','),
  );
  const contents = [header.map(csvCell).join(','), ...rows].join('\r\n');
  saveTextFile(`callback-applications-${todayIso()}.csv`, contents, 'text/csv;charset=utf-8');
}
