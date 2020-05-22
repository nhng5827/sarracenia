
from base64 import b64decode, b64encode
from codecs import decode,encode

import copy
import logging
import os
import sarra.config
import time
import types
import urllib

from sarra.plugin import Plugin

from sarra.sr_util import nowflt

logger = logging.getLogger( __name__ )

class Message:

    def __init__(self, h):
        """
         in v3, a message is just a dictionary. in v2 it is an object.
         build from sr_message.
        """
        # FIXME: new_baseurl, new_relpath, new_path ... ?

        self.pubtime=h['pubTime'].replace("T","")
        self.baseurl=h['baseUrl']
        self.relpath=h['relPath']
        self.topic=h['topic']
        if 'new_dir' in h:
            self.new_dir=h['newDir']
            self.new_file=h['newFile']

        self.urlstr= self.baseurl + self.relpath
        self.url = urllib.parse.urlparse(self.urlstr)

        self.notice=self.pubtime + ' ' + h["baseUrl" ] + ' ' + h["relPath"].replace( ' ','%20').replace('#','%23')
        del h["pubTime"]
        del h["baseUrl"]
        del h["relPath"]

        #FIXME: ensure headers are < 255 chars.
        for k in [ 'mtime', 'atime' ]:
            h[ k ] = h[k].replace("T","")

        #FIXME: sum header encoding.
        if 'size' in h:
            h[ 'parts' ] = '1,%d,1,0,0' % h['size']
            del h['size']

        if 'blocks' in h:
            if h['parts'] == 'inplace': 
                m='i'
            else: 
                m='p'
            p=h['blocks']
            h[ 'parts' ] = '%s,%d,%d,%d,%d' % ( m, p['size'], p['count'], 
                  p['remainder'], p['number'] )
            del h['blocks']

        if 'content' in h:  #v02 does not support inlining
            del h['content']

        if 'integrity' in h:
            sum_algo_v3tov2 = { "arbitrary":"a", "md5":"d", "sha512":"s", 
                "md5name":"n", "random":"0", "link":"L", "remove":"R", "cod":"z" }
            sa = sum_algo_v3tov2[ h[ "integrity" ][ "method" ] ]

            # transform sum value
            if sa in [ '0' ]:
                sv = h[ "integrity" ][ "value" ]
            elif sa in [ 'z' ]:
                sv = sum_algo_v3tov2[ h[ "integrity" ][ "value" ] ]
            else:
                sv = encode( decode( h[ "integrity" ][ "value" ].encode('utf-8'), "base64" ), 'hex' ).decode( 'utf-8' )
            h[ "sum" ] = sa + ',' + sv
            del h['integrity']

        self.headers=h
        self.hdrstr=str(h)


    def set_hdrstr(self):
        logger.info("set_hdrstr not implemented")
        pass

    def get_elapse(self):
        logger.info("get elapse not implemented")
        return 1.0
        pass

    def set_parts():
        logger.info("set_parts not implemented")
        pass

def v02tov03message( body, headers, topic ):
        msg = headers
        msg[ 'topic' ] = topic
        if not '_deleteOnPost' in headers:
            msg[ '_deleteOnPost' ] = [ 'topic' ]

        pubtime, baseurl, relpath = body.split(' ')[0:3]
        msg[ 'pubTime' ] = timev2tov3str( pubtime )
        msg[ 'baseUrl' ] = baseurl.replace( '%20',' ').replace('%23','#')
        msg[ 'relPath' ] = relPath
        for t in [ 'atime', 'mtime' ]:
            if t in msg:
                msg[ t ] = timev2tov3str( msg[ t ] )

        if 'sum' in msg:
            sum_algo_map = { "a":"arbitrary", "d":"md5", "s":"sha512", 
               "n":"md5name", "0":"random", "L":"link", "R":"remove", "z":"cod" }
            sm = sum_algo_map[ msg["sum"][0] ]
            if sm in [ 'random' ] :
                sv = msg["sum"][2:]
            elif sm in [ 'cod' ] :
                sv = sum_algo_map[ msg["sum"][2:] ]
            else:
                sv = encode( decode( msg["sum"][2:], 'hex'), 'base64' ).decode('utf-8').strip()
            msg[ "integrity" ] = { "method": sm, "value": sv }
            del msg['sum']


        if 'parts' in msg:
            ( style, chunksz, block_count, remainder, current_block ) = msg['parts'].split(',')
            if style in [ 'i' , 'p' ]:
                msg['blocks'] = {}
                msg['blocks']['method'] = {'i': 'inplace', 'p': 'partitioned'}[style]
                msg['blocks']['size'] = str(chunksz)
                msg['blocks']['count'] = str(block_count)
                msg['blocks']['remainder'] = str(remainder)
                msg['blocks']['number'] = str(current_block)
            else:
                msg['size'] = chunksz
            del msg['parts']
     
        return msg
 

class V2Wrapper(Plugin):

    def __init__(self, o):
        """
           A wrapper class to run v02 plugins.
           us run(entry_point,module)

           entry_point is a string like 'on_message',  and module being the one to add.

        """
        global logger

        # FIXME, insert parent fields for v2 plugins to use here.
        self.logger=logger
        self.logger.error('v2wrapper init start')

        self.user_cache_dir=sarra.config.get_user_cache_dir()
        self.instance = o.no
        self.o = o
        self.v2plugins = {}
        for ep in sarra.config.Config.v2entry_points:
             self.v2plugins[ep] = []
        
        for e in o.v2plugins:
            logger.info('resolving: %s' % e)
            for v in o.v2plugins[e]:
                self.add( e, v )
 
        self.logger.error('v2wrapper init done')


    def declare_option(self,option):
        logger.info('v2plugin option: %s declared' % option)

    def add(self, opname, path):

        setattr(self,opname,None)

        if path == 'None' or path == 'none' or path == 'off':
             logger.debug("Reset plugin %s to None" % opname )
             exec( 'self.' + opname + '_list = [ ]' )
             return True

        ok,script = sarra.config.config_path('plugins',path,mandatory=True,ctype='py')
        if ok:
            logger.debug("installing %s %s" % (opname, script ) )
        else:
            logger.error("installing %s %s failed: not found " % (opname, path) )
            return False

        logger.debug('installing: %s %s' % ( opname, path ) )

        try:
            with open(script) as f:
                exec(compile(f.read().replace('self.plugin','self.v2plugin'), script, 'exec'))
        except:
            logger.error("sr_config/execfile 2 failed for option '%s' and plugin '%s'" % (opname, path))
            logger.debug('Exception details: ', exc_info=True)
            return False

        if getattr(self,opname) is None:
            logger.error("%s plugin %s incorrect: does not set self.%s" % (opname, path, opname ))
            return False

        if opname == 'plugin' :

            pci = self.v2plugin.lower()
            exec( pci + ' = ' + self.v2plugin + '(self)' )
            pcv = eval( 'vars('+ self.v2plugin +')' )
            for when in sarra.config.Config.v2entry_points:
                if when in pcv:
                    logger.debug("v2 registering %s from %s" % ( when, path ) )
                    exec( 'self.' + when + '=' + pci + '.' + when )
                    eval( 'self.v2plugins["' + when + '"].append(' + pci + '.' + when + ')' )
        else:
            #eval( 'self.' + opname + '_list.append(self.' + opname + ')' )
            eval( 'self.v2plugins["' + opname +'"].append( self.' + opname + ')' )


        return True

    def on_messages(self,worklist):

        outgoing=[]
        for m in worklist.incoming:
            mm = copy.deepcopy(m)
            if self.run('on_message', mm):
               outgoing.append(m)
            else:
               worklist.rejected.append(m)
        # set incoming for future steps.
        worklist.incoming=outgoing

    def on_time(self, time ):
        """
           run plugins for a given entry point.
        """
        logger.info('v2 run %s' % time )
        for plugin in self.v2plugins[time]:
             logger.info('v2 plugin is: %s' % plugin )
             plugin(self.o)

    def on_housekeeping(self):
        self.on_time('on_housekeeping')

    def on_start(self):
        self.on_time('on_start')

    def on_stop(self):
        self.on_time('on_stop')

    def run(self,ep,m):
        """
           run plugins for a given entry point.
        """
        self.msg=Message(m)
        ok=True
        for plugin in self.v2plugins[ep]:
             ok = plugin(self) 
             if not ok: break

        return ok