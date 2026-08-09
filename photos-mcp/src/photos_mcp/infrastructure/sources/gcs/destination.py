"""GCS writes remain disabled until an approval-aware uploader is configured."""


class GCSWriteDisabledDestination:
    async def plan_write(self, *_args, **_kwargs):
        return {"status": "blocked", "error_code": "gcs_write_not_configured"}

    async def execute_write(self, *_args, **_kwargs):
        raise PermissionError("GCS destination is not configured")
