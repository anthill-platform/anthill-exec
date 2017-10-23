import common.access

from tornado.web import HTTPError
from tornado.gen import coroutine, Return

from common.access import scoped, AccessToken
from common.internal import InternalError
from common.validate import validate
import common.handler

from model.util import APIError
from model.build import JavascriptBuildError, JavascriptSessionError
from model.sources import SourceCodeError, NoSuchSourceError, JavascriptSourceError
from common.jsonrpc import JsonRPCError

import ujson
import logging


class CallSessionHandler(common.handler.JsonRPCWSHandler):
    def __init__(self, application, request, **kwargs):
        super(CallSessionHandler, self).__init__(application, request, **kwargs)
        self.session = None

    def required_scopes(self):
        return ["exec_func_call"]

    def check_origin(self, origin):
        return True

    @coroutine
    def prepared(self, application_name, application_version, class_name):
        yield super(CallSessionHandler, self).prepared(application_name, application_version, class_name)

        sources = self.application.sources

        user = self.current_user
        token = user.token

        gamespace_id = token.get(AccessToken.GAMESPACE)

        try:
            session_args = ujson.loads(self.get_argument("args", "{}"))
        except (KeyError, ValueError):
            raise HTTPError(400, "Corrupted argument 'session_args'")

        try:
            source = yield sources.get_build_source(gamespace_id, application_name, application_version)
        except SourceCodeError as e:
            raise HTTPError(e.code, e.message)
        except JavascriptSourceError as e:
            raise HTTPError(e.code, e.message)
        except NoSuchSourceError:
            raise HTTPError(404, "No source found for {0}/{1}".format(application_name, application_version))

        builds = self.application.builds

        try:
            build = yield builds.get_build(source)
        except JavascriptBuildError as e:
            raise HTTPError(e.code, e.message)

        try:
            self.session = build.session(
                class_name,
                session_args,
                application_name=application_name,
                application_version=application_version,
                gamespace=gamespace_id,
                account=token.account)

        except JavascriptSessionError as e:
            raise HTTPError(e.code, e.message)
        except APIError as e:
            raise HTTPError(e.code, e.message)
        except Exception as e:
            logging.exception("Failed during session initialization")
            raise HTTPError(500, str(e))

    @coroutine
    def call(self, method_name, arguments):

        logging.info("Calling method {0}: {1}".format(
            method_name, str(arguments)
        ))

        try:
            result = yield self.session.call(method_name, arguments)
        except JavascriptSessionError as e:
            raise JsonRPCError(e.code, e.message)
        except APIError as e:
            raise JsonRPCError(e.code, e.message)
        except Exception as e:
            raise JsonRPCError(500, str(e))

        if not isinstance(result, (str, dict, list)):
            result = str(result)

        raise Return(result)

    @coroutine
    def on_closed(self):
        if self.session:
            yield self.session.release(self.close_code, self.close_reason)
            self.session = None


class CallActionHandler(common.handler.AuthenticatedHandler):
    @coroutine
    @scoped(scopes=["exec_func_call"])
    def post(self, application_name, application_version, method_name):

        builds = self.application.builds
        sources = self.application.sources

        gamespace_id = self.token.get(AccessToken.GAMESPACE)
        account_id = self.token.account

        try:
            source = yield sources.get_build_source(gamespace_id, application_name, application_version)
        except SourceCodeError as e:
            raise HTTPError(e.code, e.message)
        except JavascriptSourceError as e:
            raise HTTPError(e.code, e.message)
        except NoSuchSourceError:
            raise HTTPError(404, "No source found for {0}/{1}".format(application_name, application_version))

        try:
            build = yield builds.get_build(source)
        except JavascriptBuildError as e:
            raise HTTPError(e.code, e.message)

        try:
            args = ujson.loads(self.get_argument("args", "{}"))
        except (KeyError, ValueError):
            raise HTTPError(400, "Corrupted args, expected to be a dict or list.")

        try:
            result = yield build.call(
                method_name, args,
                application_name=application_name,
                application_version=application_version,
                gamespace=gamespace_id,
                account=account_id)

        except JavascriptSessionError as e:
            raise HTTPError(e.code, e.message)
        except APIError as e:
            raise HTTPError(e.code, e.message)
        except Exception as e:
            raise HTTPError(500, str(e))

        if not isinstance(result, (str, dict, list)):
            result = str(result)

        self.dumps(result)


class InternalHandler(object):
    def __init__(self, application):
        self.application = application

    @coroutine
    @validate(gamespace="int", function_name="str_name", method_name="str_name",
              args="json_dict", env="json_dict", application_name="str_name")
    def call_function(self, gamespace, function_name, method_name, args, env, application_name=None):

        fcalls = self.application.fcalls

        env["gamespace"] = gamespace

        try:
            result = yield fcalls.call(function_name, args,
                                       method_name=method_name,
                                       application_name=application_name,
                                       **env)

        except JavascriptSessionError as e:
            raise HTTPError(e.code, e.message)
        except APIError as e:
            raise HTTPError(e.code, e.message)
        except Exception as e:
            raise HTTPError(500, str(e))

        if not isinstance(result, (str, dict, list)):
            result = str(result)

        raise Return(result)
