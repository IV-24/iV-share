"""Permission scope vocabulary. Examples per the spec, not exhaustive or
final — extend as new tool/resource categories are added. A scope string
outside this set is still usable (PermissionManager doesn't hard-reject
unknown scopes, since extensions will define their own), but anything in
core or the standard tool set should use one of these names.
"""


class PermissionScope:
    FILESYSTEM_READ = "filesystem.read"
    # Read-only access to iV's OWN installation directory
    # (adapters/selfinspect), kept separate from filesystem.read so that
    # granting a role the ability to read repos it was pointed at does
    # not implicitly grant it the ability to read the code running it.
    SELF_INSPECT = "self.inspect"
    FILESYSTEM_WRITE = "filesystem.write"
    DATABASE_READ = "database.read"
    DATABASE_WRITE = "database.write"
    NETWORK_REQUEST = "network.request"
    CODE_EXECUTE = "code.execute"
    GITHUB_READ = "github.read"
    GITHUB_WRITE = "github.write"
    PRODUCTION_DEPLOY = "production.deploy"
    FINANCIAL_EXECUTE = "financial.execute"
    EXTENSION_INSTALL = "extension.install"
    PERMISSIONS_MANAGE = "permissions.manage"
    EXTERNAL_COMMUNICATION = "external.communication"


ALL_SCOPES = frozenset(
    v for k, v in vars(PermissionScope).items() if not k.startswith("_")
)
