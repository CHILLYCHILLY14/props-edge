import unittest
from pipeline.accuracy import final_stats,stat_key

def summary(stats,completed=True):
 return {'header':{'competitions':[{'status':{'type':{'completed':completed}}}]},'boxscore':{'players':[{'team':{'displayName':'Team'},'statistics':stats}]}}
def group(name,keys,values,player='Player',**flags):
 return {'name':name,'keys':keys,'athletes':[{'athlete':{'displayName':player},'stats':values,**flags}]}
class FinalBoxScoreTests(unittest.TestCase):
 def test_negative_yardage_combined_stats_and_return_touchdowns(self):
  payload=summary([group('passing',['completions/attempts','passingYards','passingTouchdowns'],['20/30','210','2']),group('rushing',['rushingYards','rushingTouchdowns'],['-2','0']),group('receiving',['receivingYards','receivingTouchdowns'],['0','0']),group('puntReturns',['touchdowns'],['1'])])
  result=final_stats(payload)['stats']
  def stat(name):return result[stat_key('Player','Team',name)]
  self.assertEqual(stat('Pass completions'),20);self.assertEqual(stat('Pass attempts'),30)
  self.assertEqual(stat('Pass + rush yards'),208);self.assertEqual(stat('Rushing yards'),-2)
  self.assertEqual(stat('Anytime touchdown'),1);self.assertEqual(stat('Rush + receiving touchdowns'),0)
  self.assertEqual(stat('Pass + rush + receiving touchdowns'),2)
 def test_missing_and_dnp_are_not_zero(self):
  payload=summary([group('receiving',['receivingYards'],['--']),group('passing',['passingYards'],['0'],'DNP',didNotPlay=True)])
  self.assertEqual(final_stats(payload)['stats'],{})
 def test_in_progress_scores_never_settle(self):
  self.assertEqual(final_stats(summary([group('passing',['passingYards'],['300'])],False)),{})
 def test_kicking_slashes_and_points(self):
  out=final_stats(summary([group('kicking',['fieldGoalsMade/Attempted','extraPointsMade/Attempted'],['2/3','4/4'])]))['stats']
  self.assertEqual(out[stat_key('Player','Team','Kicking points')],10)
