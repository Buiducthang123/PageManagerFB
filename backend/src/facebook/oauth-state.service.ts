import { Injectable } from '@nestjs/common';
import { randomBytes } from 'node:crypto';

const STATE_TTL_MS = 10 * 60 * 1000;

/**
 * Chong CSRF cho OAuth flow: state chi dung 1 lan va het han sau 10 phut.
 * In-memory la du cho Phase 0 (1 instance, khong can Redis o day).
 */
@Injectable()
export class OAuthStateService {
  private readonly pending = new Map<string, number>();

  create(): string {
    const state = randomBytes(16).toString('hex');
    this.pending.set(state, Date.now() + STATE_TTL_MS);
    return state;
  }

  consume(state: string | undefined): boolean {
    if (!state) return false;
    const expiresAt = this.pending.get(state);
    this.pending.delete(state);
    if (!expiresAt) return false;
    return Date.now() <= expiresAt;
  }
}
