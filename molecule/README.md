# Integration test

`molecule test` deploys this role on three Ubuntu LXD guests and checks that one
node can ping the other through Nebula.

Run `lxd init --auto` on a new LXD server to set up storage and a bridged default
profile. Guests need outbound access and the LXD host needs `/dev/net/tun`, which
the test passes into each guest for Nebula. Run from the checkout with Python
3.12–3.14, venv support and LXD access (the `lxd` group on a local Snap install):

```sh
lxc project create local:nebula-ansible-testing -c features.images=false -c features.profiles=false
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r molecule/requirements.txt
molecule test
```

The flags share the default project's image cache and profiles. Use a dedicated
project for each concurrent run. Set `NEBULA_TEST_PROJECT` or `NEBULA_TEST_REMOTE`
to use a different dedicated project or an authenticated LXD remote.

To inspect a failure before clean-up, run `molecule test --destroy=never`, then
inspect the guests with `lxc list local: --project nebula-ansible-testing` and `lxc exec`.
Run `molecule destroy` when done.
