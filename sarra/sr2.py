#!/usr/bin/env python3

#
# This file is part of sarracenia.
# The sarracenia suite is Free and is proudly provided by the Government of Canada
# Copyright (C) Her Majesty The Queen in Right of Canada, Shared Services Canada, 2019
#

"""
   parallel version of sr. Generates a global state, then performs an action.
   previous version would, recursion style, launch individual components.

"""
import os
import os.path
import psutil
import appdirs
import pathlib
import getpass
import time
import signal
import sys
import subprocess

def ageoffile(lf):
    """ return number of seconds since a file was modified as a floating point number of seconds.
        FIXME: mocked here for now. 
    """
    return(0);

def _parse_cfg(cfg):
    """ return configuration file as a dictionary.
        FIXME: this is extremely rudimentary, doesn't do variable substitution, etc...
               only want to use this to get 'instances' for now which should be ok 99% of the time.
    """
    cfgbody={}
    for l in open(cfg,"r").readlines():
       line = l.split()
       if (len(line) < 1 ) or (  line[0] == '#' ):
          continue
       
       cfgbody[line[0]] = ' '.join(line[1:]) 
    return cfgbody


class sr_GlobalState:

    """
       build a global state of all sarra processes running on the system for this user.
       makes three data structures:  procs, configs, and states, indexed by component
       and configuration name.

       self.(procs|configs|states)[ component ][ config ]  something...

       naming: routines that start with *read* don't modify anything on disk.
               routines that start with clean do...
          
    """
    def _find_component_path( self, c ):
        """
            return the string to be used to run a component in Popen.
        """
        if c[0] != 'c' : #python components
           s =  self.bin_dir + os.sep + 'sr_' + c
           if not os.path.exists(s) :
              s+='.py'
           if not os.path.exists(s) :
              print( "don't know where the script files are for: %s" % ( c ) )
              return ''
           return(s)
        else: #C components
           return('sr_' + c)


    def _launch_instance( self, component_path, c, cfg, i ):
        """
          start up a instance process (always daemonish/background fire & forget type process.)
        """
        lfn = self.user_cache_dir + os.sep + 'log' + os.sep + 'sr_' + c + '_' + cfg + "_%02d" % i + '.log'

        if c[0] != 'c' : #python components
           cmd =  [ sys.executable,  component_path , '--no', "%d" % i , 'start', cfg ]
        else: #C components
           cmd =  [ component_path , 'start', cfg ]

        #print( "launching +%s+  re-directed to: %s" % ( cmd, lfn ) ) 

        with open( lfn, "a" ) as lf:
            subprocess.Popen( cmd, stdin=subprocess.DEVNULL, stdout=lf, stderr=subprocess.STDOUT )


    def _read_procs(self):
        # read process table.
        self.procs={}
        me=getpass.getuser()
        for proc in psutil.process_iter():
            p = proc.as_dict()
            if 'python' in p['name'] :
              n=os.path.basename(p['cmdline'][1]) 
            else:
              n=p['name']
            if ( n.startswith( 'sr_' ) and ( me == p['username'] ) ):
                self.procs[proc.pid] = p
                self.procs[proc.pid]['claimed'] = False
             

    def _read_configs(self):
        # read in configurations.
        self.configs={}
        os.chdir(self.user_config_dir)
       
        for c in self.components:
            if os.path.isdir(c):
               os.chdir(c)
               self.configs[c] = {}
               for cfg in os.listdir() :
                   
                   if cfg[-4:] == '.stopped'  :
                       cbase = cfg[0:-4]
                       state = 'disabled'
                   elif cfg[-5:] == '.conf':
                       cbase = cfg[0:-5]
                       state = 'stopped'
                   else:
                       cbase = cfg
                       state = 'unknown'

                   if state != 'unknown':
                       self.configs[c][cbase] = {}
                       self.configs[c][cbase]['status'] = state 
                       cfgbody = _parse_cfg( cfg )
  
                       # ensure there is a known value of instances to run.
                       if ( c in [ 'post', 'cpost' ] ):
                           if  ( 'sleep' in cfgbody ) and ( cfgbody['sleep'][0] not in [ '-' , '0' ] ) : 
                               numi=1
                           else: 
                               numi=0
                       elif 'instances' in cfgbody:                        
                           numi = int(cfgbody['instances']) 
                       else:
                           numi=1

                       self.configs[c][cbase]['instances']  = numi

               os.chdir('..')
   
    
    def _read_states(self):
        # read in state files
        os.chdir(self.user_cache_dir)
        self.states  = {}

        for c in self.components:
            if os.path.isdir(c):
                os.chdir(c)
                self.states[c] = {}
                for cfg in os.listdir():
                   if os.path.isdir(cfg):
                       os.chdir(cfg)
                       self.states[c][cfg]={}
                       self.states[c][cfg]['instance_pids']={}
                       self.states[c][cfg]['queue_name']=None
                       self.states[c][cfg]['instances_expected']=0
                       self.states[c][cfg]['has_state']=False

                       #print( 'state %s/%s' % ( c, cfg ) )
                       for f in os.listdir():
                            t = pathlib.Path(f).read_text().strip()
                           
                            #print( 'read f:%s len: %d contents:%s' % ( f, len(t), t[0:10] ) )
                            if len(t) == 0:
                                continue

                            #print( 'read f[-4:] = +%s+ ' % ( f[-4:] ) )
                            if f[-4:] == '.pid':
                                i = int(f[-6:-4])
                                if t.isdigit():
                                    #print( "%s/%s instance: %s, pid: %s" % 
                                    #     ( c, cfg, i, t ) )
                                    self.states[c][cfg]['instance_pids'][i]= int( t )
                            elif f[-6:] == '.qname' :
                                self.states[c][cfg]['queue_name'] = t
                            elif f[-6:] == '.state' and ( f[-12:-6] != '.retry' ):
                                if t.isdigit():
                                    self.states[c][cfg]['instances_expected'] = int ( t )
                       os.chdir('..')
                os.chdir('..')

    def _find_missing_instances(self): 
        """ find processes which are no longer running, based on pidfiles in state, and procs.
        """
        os.chdir(self.user_cache_dir)
        missing=[]
        for c in self.components:
            if os.path.isdir(c):
                os.chdir(c)
                for cfg in os.listdir():
                   if os.path.isdir(cfg):
                       os.chdir(cfg)
                       for f in os.listdir():
                            if f[-4:] == '.pid':
                               i = int(f[-6:-4])
                               t = pathlib.Path(f).read_text().strip()
                               if t.isdigit():
                                   pid = int( t )
                                   if pid not in self.procs:
                                       missing.append( [ c, cfg, i ] )
                               else:
                                   missing.append( [ c, cfg, i ] )

                       os.chdir('..')
                os.chdir('..')

        self.missing = missing

    

    def _clean_missing_proc_state(self): 
        """ remove state pid files for process which are not running
        """

        os.chdir(self.user_cache_dir)
        for instance in self.missing:
            ( c, cfg, i ) = instance
            if os.path.isdir(c):
                os.chdir(c)
                for cfg in os.listdir():
                   if os.path.isdir(cfg):
                       os.chdir(cfg)
                       for f in os.listdir():
                            if f[-4:] == '.pid':
                               t = pathlib.Path(f).read_text().strip()
                               if t.isdigit():
                                   pid = int( t )
                                   if pid not in self.procs:
                                       os.unlink(f)
                               else:
                                   os.unlink(f)

                       os.chdir('..')
                os.chdir('..')
               

    def _read_logs(self):

        os.chdir(self.user_cache_dir)
        if os.path.isdir('log'):
           self.logs={}
           for c in self.components:
              self.logs[c]={}
              
           os.chdir('log')

           for lf in os.listdir():
              lff = lf.split('_')
              #print('looking at: %s' %lf )
              if len(lff) > 3 :
                  c = lff[1]
                  cfg = '_'.join(lff[2:-1])
                  suffix = lff[-1].split('.')
               
                  if suffix[1] == 'log':
                      inum = int(suffix[0])
                      age = ageoffile(lf)
                      if not cfg in self.logs[c]:
                         self.logs[c][cfg]={}
                      self.logs[c][cfg][inum]=age


    def _resolve(self):
        """
           compare configs, states, & logs and fill things in.

           things that could be identified: differences in state, running & configured instances.
        """

        # comparing states and configs to find missing instances, and correct state.
        for c in self.components:
            if (c not in self.states) or (c not in self.configs):
                  continue

            for cfg in self.configs[c]:
               if not cfg in self.states[c]:
                  print('missing state for sr_%s/%s' % (c,cfg) )
                  continue
               if len(self.states[c][cfg]['instance_pids']) > 0:
                  self.states[c][cfg]['missing_instances'] = []
                  observed_instances=0
                  for i in self.states[c][cfg]['instance_pids']:
                      if self.states[c][cfg]['instance_pids'][i] not in self.procs:
                         self.states[c][cfg]['missing_instances'].append(i) 
                      else:
                         observed_instances+=1
                         self.procs[ self.states[c][cfg]['instance_pids'][i] ]['claimed'] = True

                  if observed_instances < self.states[c][cfg]['instances_expected']:
                      print( "%s/%s observed_instances: %s expected: %s" % \
                         ( c, cfg, observed_instances, self.states[c][cfg]['instances_expected'] ) )
                      self.configs[c][cfg]['status'] = 'partial'
                  else:
                      self.configs[c][cfg]['status'] = 'running'
            # check for too many instances.
            
         

    def __init__(self):
        """
           side effect: changes current working directory FIXME?
        """

        self.appname   = 'sarra'
        self.appauthor = 'science.gc.ca'
        self.user_config_dir = appdirs.user_config_dir( self.appname, self.appauthor )
        self.user_cache_dir  = appdirs.user_cache_dir (self.appname,self.appauthor)
        self.components = [ 'audit', 'cpost', 'cpump', 'poll', 'post', 'report', 'sarra', 'sender', 'shovel', 'subscribe', 'watch', 'winnow' ]
        self.status_values = [ 'stopped', 'partial', 'running' ]
  
        self.bin_dir = os.path.dirname( os.path.realpath(__file__) )

        print('gathering global state...')
        self._read_procs()
        self._read_configs() 
        self._read_states() 
        self._read_logs() 
        self._resolve()
        self._find_missing_instances()

    def _start_missing(self):
        for instance in self.missing:
            ( c, cfg, i ) = instance
            component_path = self._find_component_path(c)
            if component_path == '':
               continue
            self._launch_instance( component_path, c, cfg, i )
 
    def sanity(self):
        self._find_missing_instances()
        print( 'missing: %s' % self.missing )
        print( 'starting them up...')
        self._start_missing()
        

    def start(self):

        if len(self.procs) > 0:
           print('already started')
           return

        for c in self.components:
            if (c not in self.configs):
               continue
            component_path = self._find_component_path(c)
            if component_path == '':
               continue
            for cfg in self.configs[c]:
               #print('in start: component/cfg: %s/%s' % (c,cfg))
               if self.configs[c][cfg]['status'] in [ 'stopped' ]:
                  numi = self.configs[c][cfg]['instances']
                  for i in range(1,numi+1):
                      print( '.', end='' )
                      self._launch_instance( component_path, c, cfg, i )
        print('Done')
        #FIXME: sr_audit


    def stop(self):

        """
           stop all of this users sr_ processes. 
           return 0 on success, non-zero on failure.
        """
        self._clean_missing_proc_state()

        if len(self.procs) == 0:
           print('already stopped')
           return

        for c in self.components:
            if (c not in self.configs):
               continue
            for cfg in self.configs[c]:
               if self.configs[c][cfg]['status'] in [ 'running', 'partial' ]:
                  for i in self.states[c][cfg]['instance_pids']:
                      #print( "for %s/%s - %s os.kill( %s, SIGTERM )" % \
                      #    ( c, cfg, i, self.states[c][cfg]['instance_pids'][i] ) )
                      if self.states[c][cfg]['instance_pids'][i] in self.procs:
                          os.kill( self.states[c][cfg]['instance_pids'][i], signal.SIGTERM )
                          print( '.', end='' )

        print('Done')

        for pid in self.procs:
            if not self.procs[pid]['claimed']:
                print( "pid: %s-%s does not match any configured instance, sending it TERM" %  (pid, self.procs[pid]['cmdline'][0:5]) )
                os.kill( pid, signal.SIGTERM )
                
        print( 'Waiting to check if they stopped' )
        time.sleep(5)
        # update to reflect killed processes.
        self._read_procs()
        self._find_missing_instances()
        self._clean_missing_proc_state()
        self._read_states()
        self._resolve()
        
        if len( self.procs ) == 0:
            print( 'All stopped after first try' )
            return 0

        print( 'doing SIGKILL this time...' )
        for c in self.components:
            if (c not in self.configs):
               continue
            for cfg in self.configs[c]:
               if self.configs[c][cfg]['status'] in [ 'running', 'partial' ]:
                   for i in self.states[c][cfg]['instance_pids']:
                       if self.states[c][cfg]['instance_pids'][i] in self.procs:
                           print( "os.kill( %s, SIGKILL )" % self.states[c][cfg]['instance_pids'][i] )
                           os.kill( self.states[c][cfg]['instance_pids'][i], signal.SIGKILL )
                           print( '.', end='' )

        print('Done')

        for pid in self.procs:
            if not self.procs[pid]['claimed']:
                print( "pid: %s-%s does not match any configured instance, would kill" %  (pid, self.procs[pid]['cmdline']) )
                os.kill( pid, signal.SIGKILL )

        print( 'Waiting again...' )
        time.sleep(2)
        self._read_procs()
        self._find_missing_instances()
        self._clean_missing_proc_state()
        self._read_states()
        self._resolve()

        for c in self.components:
            if (c not in self.configs):
               continue
            for cfg in self.configs[c]:
               if self.configs[c][cfg]['status'] in [ 'running', 'partial' ]:
                   for i in self.states[c][cfg]['instance_pids']:
                       print( "failed to kill: %s/%s instance: %s, pid: %s )" % (c, cfg, i, self.states[c][cfg]['instance_pids'][i] ) )
        if len( self.procs ) == 0:
            print( 'All stopped after second try' )
            return 0
        else:
            print( 'not responding to SIGKILL:' )
            for p in self.procs:
                print( '\t%s: %s' % (pid, self.procs[pid]['cmdline'][0:5]) )
            return 1


    def dump(self):

        print( '\n\nRunning Processes\n\n' )
        for pid in self.procs:
            print( '\t%s: name:%s cmdline:%s' % (pid, self.procs[pid]['name'], self.procs[pid]['cmdline']) )

        print( '\n\nConfigs\n\n' )
        for c in self.configs:
           print( '\t%s ' %c )
           for cfg in self.configs[c]:
               print( '\t\t%s : %s' % (cfg, self.configs[c][cfg] ) )

        print( '\n\nStates\n\n' )
        for c in self.states:
           print( '\t%s ' %c )
           for cfg in self.states[c]:
               print( '\t\t%s : %s' % (cfg, self.states[c][cfg] ) )


    def status(self):

        for c in self.configs:
            status={}
            for sv in self.status_values:
                status[ sv ] =[]

            for cfg in self.configs[c]:
                status[ self.configs[c][cfg]['status'] ].append( cfg )
                   
            if (len(status['partial'])+len(status['running'])) < 1:
                   print( 'sr_%s: all stopped' % c ) 
            elif ( len(status['running']) == len(self.configs[c]) ):
                   print( 'sr_%s: all running' % c ) 
            else:
                print( 'sr_%s: mixed status' % c )
                for sv in self.status_values:
                    if len(status[sv]) > 0:
                       print( '%10s: %s ' % ( sv, ', '.join(status[ sv ]) ) )

        for pid in self.procs:
            if not self.procs[pid]['claimed']:
                print( "pid: %s-%s is not a configured instance" %  (pid, self.procs[pid]['cmdline']) )
            


def main():

   actions_supported = [ 'status' ]

   gs = sr_GlobalState()   

   if len(sys.argv) < 2:
       action='status'
   else:
       action=sys.argv[1]


   if action == 'dump' :
      print('dumping...')
      gs.dump()

   elif action == 'restart' :
      print('restarting...')
      gs.stop()
      gs.start()

   elif action == 'sanity' :
      print('sanity...')
      gs.sanity()

   elif action == 'start' :
      print('starting...')
      gs.start()

   elif action == 'status' :
       print('status...')
       gs.status()

   elif action == 'stop' :
      print('stopping...')
      gs.stop()



if __name__ == "__main__":
   main()
