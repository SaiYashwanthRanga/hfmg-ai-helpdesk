const RTF = new Intl.RelativeTimeFormat("en", { numeric: "auto" });

const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 60 * 60 * 24 * 365],
  ["month", 60 * 60 * 24 * 30],
  ["day", 60 * 60 * 24],
  ["hour", 60 * 60],
  ["minute", 60],
];

/** "2h ago" style relative timestamp — table cells pair this with the absolute time in a `title` attribute. */
export function formatRelativeTime(isoDate: string): string {
  const diffSeconds = (new Date(isoDate).getTime() - Date.now()) / 1000;

  for (const [unit, secondsInUnit] of UNITS) {
    if (Math.abs(diffSeconds) >= secondsInUnit) {
      return RTF.format(Math.round(diffSeconds / secondsInUnit), unit);
    }
  }
  return RTF.format(Math.round(diffSeconds / 60), "minute");
}

export function formatAbsoluteTime(isoDate: string): string {
  return new Date(isoDate).toLocaleString();
}
