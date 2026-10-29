import unittest

try:
    import textual  # noqa: F401
    HAVE_TEXTUAL = True
except ImportError:
    HAVE_TEXTUAL = False


@unittest.skipUnless(HAVE_TEXTUAL, "textual not installed")
class TestDashboard(unittest.IsolatedAsyncioTestCase):
    async def test_starts_and_encode_with_no_source_does_not_crash(self):
        from tui.app import PixelVaultApp
        app = PixelVaultApp()
        async with app.run_test() as pilot:
            await pilot.click("#encode")
            await pilot.pause()
            self.assertTrue(app.is_running)
            buttons = list(app.query("Button"))
            self.assertTrue(all(not b.disabled for b in buttons))

    async def test_restore_with_missing_folder_does_not_crash(self):
        from tui.app import PixelVaultApp
        app = PixelVaultApp()
        async with app.run_test() as pilot:
            app.query_one("#tiles").value = "Z:/definitely/not/here"
            app.query_one("#dest").value = "Z:/restore"
            await pilot.click("#restore")
            await pilot.pause()
            self.assertTrue(app.is_running)


if __name__ == "__main__":
    unittest.main()
