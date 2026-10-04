// Debug data is permitted only for the synthetic laboratory. Keep it bounded and
// remove common credential forms; no storageState, headers, or process env dump.
export function safeDiagnostic(value, limit = 2048) {
  return String(value ?? '')
    .replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, '')
    .replace(/(?:authorization|cookie|set-cookie)\s*[:=][^\r\n]*/gi, '[credential-header-redacted]')
    .replace(/\b(?:token|secret|password|credential|api[_-]?key)\s*[:=]\s*[^\s,;]+/gi, '[credential-redacted]')
    .replace(/(https?:\/\/)[^\s/@]+:[^\s/@]+@/gi, '$1[credentials-redacted]@')
    .replace(/(\/devtools\/(?:browser|page)\/)[A-Fa-f0-9-]+/g, '$1[target-redacted]')
    .replace(/([?&](?:token|key|secret|auth)[^=\s]*=)[^&\s]+/gi, '$1[redacted]')
    .slice(0, limit);
}
