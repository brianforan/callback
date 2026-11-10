import type { QueryClient } from '@tanstack/react-query';

/**
 * Drops everything cached for the account, except who's signed in. Used when the session ends
 * or a different account signs in, so one account's data is never shown to the next.
 */
export function clearAccountData(client: QueryClient) {
  client.removeQueries({ predicate: (query) => query.queryKey[0] !== 'me' });
}
