import { QueryClient } from '@tanstack/react-query';
import { describe, expect, it } from 'vitest';

import { clearAccountData } from './cache';

describe('signing in as someone else', () => {
  it("drops everything cached for the last account except who's signed in", () => {
    const client = new QueryClient();
    client.setQueryData(['me'], { id: 'b' });
    client.setQueryData(['applications'], [{ company: "A's job" }]);
    client.setQueryData(['profiles'], { profiles: [{ id: 'a-profile' }] });
    client.setQueryData(['postings', 'p1'], { id: 'p1' });

    clearAccountData(client);

    expect(client.getQueryData(['me'])).toEqual({ id: 'b' });
    expect(client.getQueryData(['applications'])).toBeUndefined();
    expect(client.getQueryData(['profiles'])).toBeUndefined();
    expect(client.getQueryData(['postings', 'p1'])).toBeUndefined();
  });
});
