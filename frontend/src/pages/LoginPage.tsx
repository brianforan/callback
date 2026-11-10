import {
  Alert,
  Anchor,
  Button,
  Card,
  Center,
  Group,
  PasswordInput,
  Stack,
  Text,
  TextInput,
  ThemeIcon,
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { IconPhoneIncoming } from '@tabler/icons-react';
import { useState } from 'react';
import { Navigate } from 'react-router-dom';

import { useAuthConfig, useLogin, useMe, useRegister } from '../lib/queries';

const MIN_PASSWORD_LENGTH = 12;

export function LoginPage() {
  const { data: user } = useMe();
  const { data: config } = useAuthConfig();
  const login = useLogin();
  const register = useRegister();
  const [isRegistering, setIsRegistering] = useState(false);

  const form = useForm({
    initialValues: { email: '', password: '', confirm: '' },
    validate: {
      password: (value) =>
        isRegistering && value.length < MIN_PASSWORD_LENGTH
          ? `Use at least ${MIN_PASSWORD_LENGTH} characters`
          : null,
      confirm: (value, values) =>
        isRegistering && value !== values.password ? 'Passwords do not match' : null,
    },
  });

  if (user) return <Navigate to="/" replace />;

  const active = isRegistering ? register : login;

  function switchMode() {
    setIsRegistering((current) => !current);
    form.setValues({ password: '', confirm: '' });
    form.clearErrors();
    login.reset();
    register.reset();
  }

  return (
    <Center mih="100dvh" className="app-main" p="md">
      <Card w={360} maw="100%" padding="lg">
        <form
          onSubmit={form.onSubmit(({ email, password }) => active.mutate({ email, password }))}
        >
          <Stack gap="sm">
            <Group gap="xs">
              <ThemeIcon size="md" radius="md">
                <IconPhoneIncoming size={16} />
              </ThemeIcon>
              <Text fw={700}>Callback</Text>
            </Group>
            <Text size="sm" c="dimmed">
              See which applications and resumes get you a call back.
            </Text>
            {active.isError && (
              <Alert color="red" variant="light" p="xs">
                {active.error.message}
              </Alert>
            )}
            <TextInput
              label="Email"
              type="email"
              autoComplete="username"
              required
              {...form.getInputProps('email')}
            />
            <PasswordInput
              label="Password"
              description={isRegistering ? `At least ${MIN_PASSWORD_LENGTH} characters` : undefined}
              autoComplete={isRegistering ? 'new-password' : 'current-password'}
              required
              {...form.getInputProps('password')}
            />
            {isRegistering && (
              <PasswordInput
                label="Repeat password"
                autoComplete="new-password"
                required
                {...form.getInputProps('confirm')}
              />
            )}
            <Button type="submit" loading={active.isPending} fullWidth>
              {isRegistering ? 'Create account' : 'Sign in'}
            </Button>
            {config?.registration_enabled && (
              <Text size="xs" c="dimmed" ta="center">
                {isRegistering ? 'Already have an account? ' : 'New here? '}
                <Anchor component="button" type="button" size="xs" onClick={switchMode}>
                  {isRegistering ? 'Sign in' : 'Create an account'}
                </Anchor>
              </Text>
            )}
          </Stack>
        </form>
      </Card>
    </Center>
  );
}
