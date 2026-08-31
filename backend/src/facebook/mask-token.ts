export function maskToken(token: string): string {
  if (token.length <= 8) return '****';
  return `${token.slice(0, 6)}...${token.slice(-4)}`;
}
