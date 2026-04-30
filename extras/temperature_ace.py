# Optional ACE temperature sensor for Klipper.
# Reads the ACE device temperature reported in each AceInstance status
# and exposes it as a standard temperature_sensor.

import logging

ACE_REPORT_TIME = 1.0  # seconds between samples
_REGISTERED = False


class TemperatureACE:
    """
    Temperature sensor that reads ACE device temperature from an AceInstance.

    Configuration example:
        [temperature_sensor ace_temp]
        sensor_type: temperature_ace
        ace_instance: 0        # which ACE instance to read (default 0)
        min_temp: 0
        max_temp: 70
    """

    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.name = config.get_name().split()[-1]
        self.instance_num = config.getint("ace_instance", 0)

        # Temperature state
        self.temp = 0.0
        self.min_temp = 0.0
        self.max_temp = 70.0
        self.measured_min = float("inf")
        self.measured_max = 0.0

        # ACE references resolved on ready
        self._ace_manager = None
        self._ace_instance = None

        # Klipper temperature callback
        self._callback = None

        # Register object
        self.printer.add_object("temperature_ace " + self.name, self)

        # Skip timers in debug mode
        if self.printer.get_start_args().get("debugoutput") is not None:
            return

        # Periodic sampling timer and event hooks
        self.sample_timer = self.reactor.register_timer(self._sample_ace_temperature)
        self.printer.register_event_handler("klippy:connect", self.handle_connect)
        self.printer.register_event_handler("klippy:ready", self.handle_ready)

    def handle_ready(self):
        """Resolve ACE instance references once Klipper is ready."""
        self._ace_manager = self.printer.lookup_object("ace", None)
        self._ace_instance = self._resolve_instance(self.instance_num)

        if self._ace_instance:
            logging.info(
                "temperature_ace: linked to ACE instance %d (name=%s)",
                self.instance_num,
                getattr(self._ace_instance, "instance_num", self.instance_num),
            )
        else:
            logging.warning(
                "temperature_ace: no ACE instance %d found; reporting 0°C",
                self.instance_num,
            )

        if hasattr(self, "sample_timer"):
            self.reactor.update_timer(self.sample_timer, self.reactor.NOW)

    def handle_connect(self):
        """Start sampling on Klipper connect."""
        if hasattr(self, "sample_timer"):
            self.reactor.update_timer(self.sample_timer, self.reactor.NOW)

    def setup_minmax(self, min_temp, max_temp):
        """Required by heaters interface."""
        self.min_temp = min_temp
        self.max_temp = max_temp

    def setup_callback(self, cb):
        """Required by heaters interface."""
        self._callback = cb

    def get_report_time_delta(self):
        """Required by heaters interface."""
        return ACE_REPORT_TIME

    def _resolve_instance(self, inst_num):
        """Best-effort lookup of the requested AceInstance."""
        if self._ace_manager and hasattr(self._ace_manager, "instances"):
            try:
                return self._ace_manager.instances[inst_num]
            except Exception:
                pass

        # Fallback to per-instance printer object name
        try:
            return self.printer.lookup_object(f"ace_instance_{inst_num}", None)
        except Exception:
            return None

    def _sample_ace_temperature(self, eventtime):
        """Timer callback to read ACE temperature and feed the heaters system."""
        try:
            if not self._ace_instance:
                # If the instance wasn't found earlier, retry lookup occasionally.
                self._ace_instance = self._resolve_instance(self.instance_num)

            if self._ace_instance and hasattr(self._ace_instance, "_info"):
                ace_temp = float(self._ace_instance._info.get("temp", 0.0) or 0.0)

                if not hasattr(self, "_sample_logged") and ace_temp > 0:
                    logging.info(
                        "temperature_ace: first sample from instance %d = %.1f°C",
                        self.instance_num,
                        ace_temp,
                    )
                    self._sample_logged = True

                self.temp = ace_temp

                if self.temp > 0:
                    self.measured_min = min(self.measured_min, self.temp)
                    self.measured_max = max(self.measured_max, self.temp)

                if self.temp > 0 and self.temp < self.min_temp:
                    self.printer.invoke_shutdown(
                        "ACE temperature %.1f below minimum of %.1f"
                        % (self.temp, self.min_temp)
                    )
                if self.temp > self.max_temp:
                    self.printer.invoke_shutdown(
                        "ACE temperature %.1f above maximum of %.1f"
                        % (self.temp, self.max_temp)
                    )
            else:
                if not hasattr(self, "_warning_shown"):
                    logging.warning(
                        "temperature_ace: ACE instance %d not available or has no _info",
                        self.instance_num,
                    )
                    self._warning_shown = True
                self.temp = 0.0
        except Exception:
            logging.exception("temperature_ace: error sampling ACE temperature")
            self.temp = 0.0

        if self._callback:
            mcu = self.printer.lookup_object("mcu")
            measured_time = self.reactor.monotonic()
            self._callback(mcu.estimated_print_time(measured_time), self.temp)

        return eventtime + ACE_REPORT_TIME

    def get_temp(self, eventtime):
        """Required by temperature_sensor interface."""
        return self.temp, 0.0

    def stats(self, eventtime):
        """For logging/debug output."""
        return False, "temperature_ace %s: temp=%.1f" % (self.name, self.temp)

    def get_status(self, eventtime):
        """Expose status for Moonraker/API."""
        return {
            "temperature": round(self.temp, 2),
            "measured_min_temp": round(self.measured_min, 2)
            if self.measured_min != float("inf")
            else 0.0,
            "measured_max_temp": round(self.measured_max, 2),
            "ace_instance": self.instance_num,
        }


def load_config(config):
    """Register temperature_ace sensor factory with Klipper (config hook)."""
    register_sensor_factory(config.get_printer())


def register_sensor_factory(config):
    """Idempotently register the temperature_ace sensor factory."""
    global _REGISTERED
    if _REGISTERED:
        return
    # Accept either a config wrapper or a printer object
    printer = config.get_printer() if hasattr(config, "get_printer") else config

    heaters = None
    # Try to reuse existing heaters object first
    if hasattr(printer, "lookup_object"):
        try:
            heaters = printer.lookup_object("heaters")
        except Exception:
            heaters = None

    # If not found, load it now (safe even if already loaded)
    if heaters is None and hasattr(printer, "load_object"):
        try:
            heaters = printer.load_object(config, "heaters")
        except Exception as e:
            logging.warning("temperature_ace: failed to load heaters: %s", e)
            heaters = None

    if heaters is None:
        logging.warning("temperature_ace: heaters object unavailable; sensor factory not registered")
        return

    heaters.add_sensor_factory("temperature_ace", TemperatureACE)
    _REGISTERED = True
