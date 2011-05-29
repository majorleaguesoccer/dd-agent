# Standard imports
import logging
import os
import sys

#Tornado
import tornado.httpserver
import tornado.httpclient
import tornado.ioloop
import tornado.web
from tornado.options import define, parse_command_line, options

# agent import
from config import get_config, get_system_stats, get_parsed_args
from emitter import http_emitter
from checks.common import checks


CHECK_INTERVAL =  60 * 1000 # Every 60s
PROCESS_CHECK_INTERVAL = 1000 # Every second


class AgentInputHandler(tornado.web.RequestHandler):

    @tornado.web.asynchronous
    def post(self):
        """Read the message and forward it to the intake"""

        req = tornado.httpclient.HTTPRequest(self.application.agentConfig['ddUrl'], 
            method = "POST", body = self.request.body )
        http = tornado.httpclient.AsyncHTTPClient()
        logging.info("Forwarding agent message to datadog")
        http.fetch(req, callback=self.on_response)

    def on_response(self, response):
        if response.error: 
            logging.info("done with error: " + str(response.error))
            raise tornado.web.HTTPError(500)
        logging.info("done")
        self.finish()

class Application(tornado.web.Application):

    def __init__(self,options):

        handlers = [
            (r"/intake/?", AgentInputHandler),
        ]

        settings = dict(
            cookie_secret="12oETzKXQAGaYdkL5gEmGeJJFuYh7EQnp2XdTP1o/Vo=",
            xsrf_cookies=False,
            debug=True,
        )

        self._check_pid = -1

        #Create checks instance
        agentLogger = logging.getLogger('agent')

        agentLogger.debug('Collecting basic system stats')

        systemStats = get_system_stats()
        agentLogger.debug('System: ' + str(systemStats))

        agentLogger.debug('Creating checks instance')

        self.agentConfig, self.rawConfig = get_config()
        emitter = http_emitter
       
        mConfig = dict(self.agentConfig)
        mConfig['ddUrl'] = "http://localhost:" + str(options.port)
        self._checks = checks(mConfig, self.rawConfig, emitter)

        tornado.web.Application.__init__(self, handlers, **settings)

        http_server = tornado.httpserver.HTTPServer(self)
        http_server.listen(options.port)

        self.run_checks(True,systemStats)

    def run_checks(self, firstRun = False, systemStats = False):

        if self._check_pid > 0:
            logging.warning("Not running checks because a previous instance is still running")
            return False

        child_pid = os.fork()
        try:
            if child_pid == 0: # child
                try:
                    self._checks._doChecks(firstRun, systemStats)
                except:
                    sys.exit(1)

                sys.exit(0)
            else: # parent                
                self._check_pid = child_pid

        except OSError,e:
            logging.exception(e)
            return False

        return True

    def process_check(self):

        if self._check_pid > 0:
            logging.debug("Checking on child process")
            # Try to join the process running checks
            (pid, status) = os.waitpid(self._check_pid,os.WNOHANG)
            if (pid, status) != (0, 0):
                logging.debug("child (pid: %s) exited with status: %s" % (pid, status))
                if status != 0:
                    logging.error("Error while running checks")
                self._check_pid = -1
            else:
                logging.debug("child (pid: %s) still running" % self._check_pid)

def main():
    define("port", type=int, default=17123, help="Port to listen on")
    define("log", type=str, default="ddagent.log", help="Log file to use")

    parse_command_line()

    # set up logging
    formatter = logging.Formatter(fmt="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s")
    handler = logging.FileHandler(filename=options.log)
    handler.setFormatter(formatter)
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.DEBUG)

    logging.info("Starting ddagent tornado")


    # Set up tornado
    app = Application(options)

    # Register check callback
    mloop = tornado.ioloop.IOLoop.instance() 

    def run_checks():
        logging.info("Running checks...")
        app.run_checks()
    
    def process_check():
        app.process_check()

    check_scheduler = tornado.ioloop.PeriodicCallback(run_checks,CHECK_INTERVAL, io_loop = mloop) 
    check_scheduler.start()

    p_checks_scheduler = tornado.ioloop.PeriodicCallback(process_check,PROCESS_CHECK_INTERVAL, io_loop = mloop) 
    p_checks_scheduler.start()

    # Start me up!
    mloop.start()


if __name__ == "__main__":
    main()

